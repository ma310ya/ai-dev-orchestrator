import json
import os
import tempfile
import threading
from datetime import datetime, timezone
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken

_LOCK = threading.RLock()


def _credentials_path():
    return Path(
        os.environ.get(
            "APP_CREDENTIALS_FILE",
            str(Path.home() / ".config" / "ai-dev-orchestrator" / "credentials.enc"),
        )
    )


def _fernet():
    key = os.environ.get("APP_CREDENTIALS_KEY")
    if not key:
        raise RuntimeError(
            "認証情報の暗号化鍵 APP_CREDENTIALS_KEY が未設定です。"
        )
    try:
        return Fernet(key.encode("ascii"))
    except (ValueError, UnicodeEncodeError) as error:
        raise RuntimeError(
            "APP_CREDENTIALS_KEY はFernet形式の鍵ではありません。"
        ) from error


def validate_credentials_configuration():
    _fernet()


def _read_credentials():
    path = _credentials_path()
    if not path.exists():
        return {}
    try:
        decrypted = _fernet().decrypt(path.read_bytes())
        data = json.loads(decrypted)
    except InvalidToken as error:
        raise RuntimeError(
            "認証情報ファイルを復号できません。APP_CREDENTIALS_KEYが以前の鍵と一致するか確認してください。"
        ) from error
    except json.JSONDecodeError as error:
        raise RuntimeError("認証情報ファイルの内容が壊れています。") from error
    if not isinstance(data, dict):
        raise RuntimeError("認証情報ファイルの形式が不正です。")
    return data


def get_credential(name):
    with _LOCK:
        credential = _read_credentials().get(name)
    if not isinstance(credential, dict):
        return None
    expires_at = credential.get("expires_at")
    if expires_at and expires_at <= datetime.now(timezone.utc).timestamp():
        return None
    return credential


def save_credential(name, value, expires_at=None):
    if not isinstance(value, str) or not value:
        raise ValueError("空の認証情報は保存できません。")
    with _LOCK:
        credentials = _read_credentials()
        credentials[name] = {
            "value": value,
            "expires_at": expires_at,
        }
        path = _credentials_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            path.parent.chmod(0o700)
        except OSError:
            pass
        encrypted = _fernet().encrypt(
            json.dumps(credentials, separators=(",", ":")).encode("utf-8")
        )
        temp_path = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="wb", dir=path.parent, delete=False
            ) as temporary:
                temp_path = Path(temporary.name)
                temporary.write(encrypted)
                temporary.flush()
                os.fsync(temporary.fileno())
                try:
                    os.fchmod(temporary.fileno(), 0o600)
                except OSError:
                    pass
            os.replace(temp_path, path)
            try:
                path.chmod(0o600)
            except OSError:
                pass
        finally:
            if temp_path and temp_path.exists():
                temp_path.unlink()


def delete_credential(name):
    with _LOCK:
        credentials = _read_credentials()
        if name not in credentials:
            return
        del credentials[name]
        path = _credentials_path()
        if not credentials:
            path.unlink(missing_ok=True)
            return
        encrypted = _fernet().encrypt(
            json.dumps(credentials, separators=(",", ":")).encode("utf-8")
        )
        temporary = path.with_suffix(path.suffix + ".tmp")
        try:
            temporary.write_bytes(encrypted)
            try:
                temporary.chmod(0o600)
            except OSError:
                pass
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)
