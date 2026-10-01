import numpy as np
import trimesh
import json
import argparse
import os
import sys
import logging

# ログ設定
logging.basicConfig(level=logging.INFO, format='%(levelname)s: %(message)s')

class BodySimulationEngine:
    """
    仮想的な身体構造の再構成シミュレーションエンジン
    メッシュデータの幾何学的変換および物理パラメータの算出を行う
    """

    def __init__(self, input_data):
        # 必須キーのバリデーションとデフォルト補完
        required_keys = {'height': 170, 'shoulder': 40, 'pelvis': 35}
        self.data = {key: input_data.get(key, default) for key, default in required_keys.items()}
        
        # 統計的な女性の平均比率
        self.ratios = {
            'shoulder_width': 0.85,
            'pelvis_width': 1.15,
            'height_scaling': 0.95
        }

    def process_chromosomes(self):
        """遺伝子情報の仮想的な再定義"""
        return {"chromosomes": "XX", "hormone_profile": "estrogen_dominant"}

    def apply_transform(self, mesh):
        """頂点座標に対する幾何学的変換処理（非破壊的）"""
        transformed_mesh = mesh.copy()
        
        # 変換行列の生成：肩幅(X)、身長(Y)、骨盤幅(Z)の軸に合わせてスケーリング
        scale_matrix = np.diag([
            self.ratios['shoulder_width'], 
            self.ratios['height_scaling'], 
            self.ratios['pelvis_width'],
            1.0
        ])
        transformed_mesh.apply_transform(scale_matrix)
        return transformed_mesh

    def export_mesh(self, mesh, output_path):
        """変形済みメッシュを指定パスに保存"""
        output_dir = os.path.dirname(os.path.abspath(output_path))
        if output_dir and not os.path.exists(output_dir):
            try:
                os.makedirs(output_dir)
            except OSError as e:
                logging.error(f"Could not create directory {output_dir}. {e}")
                sys.exit(1)
        
        try:
            mesh.export(output_path)
        except Exception as e:
            logging.error(f"Failed to export mesh to {output_path}. {e}")
            sys.exit(1)

    def generate_report(self):
        """シミュレーション結果の数値レポート生成"""
        genetics = self.process_chromosomes()
        report = {
            "status": "Simulation Complete",
            "genetic_profile": genetics,
            "physical_parameters": {
                "original": self.data,
                "ratios_applied": self.ratios,
                "estimated_shoulder_width": self.data['shoulder'] * self.ratios['shoulder_width'],
                "estimated_pelvis_width": self.data['pelvis'] * self.ratios['pelvis_width'],
                "estimated_height": self.data['height'] * self.ratios['height_scaling']
            }
        }
        return report

def main():
    parser = argparse.ArgumentParser(
        description="Virtual Body Simulation Tool",
        epilog="Example usage: python3 new_tool.py --config params.json --input_mesh model.obj --output_mesh result.obj"
    )
    parser.add_argument('--config', type=str, default='params.json', help="Path to params.json")
    parser.add_argument('--input_mesh', type=str, default=None, help="Input .obj or .ply file path")
    parser.add_argument('--output_mesh', type=str, default='output.obj', help="Output .obj or .ply file path")
    
    args = parser.parse_args()

    # 設定ファイルの確認と自動生成
    config_path = os.path.abspath(args.config)
    if not os.path.exists(config_path):
        logging.info(f"'{config_path}' not found. Generating default settings.")
        default_params = {"height": 170, "shoulder": 40, "pelvis": 35}
        try:
            with open(config_path, 'w') as f:
                json.dump(default_params, f, indent=4)
            input_data = default_params
        except Exception as e:
            logging.error(f"Failed to generate default config file: {e}")
            sys.exit(1)
    else:
        try:
            with open(config_path, 'r') as f:
                input_data = json.load(f)
        except json.JSONDecodeError:
            logging.error(f"Failed to decode JSON from {config_path}")
            sys.exit(1)

    try:
        engine = BodySimulationEngine(input_data)
        
        # メッシュ読み込み処理（存在しない場合はサンプル生成）
        if args.input_mesh and os.path.exists(args.input_mesh):
            mesh = trimesh.load(args.input_mesh)
            logging.info(f"Loaded mesh from {args.input_mesh}")
        else:
            logging.warning("Input mesh not specified or not found. Generating default icosphere.")
            mesh = trimesh.creation.icosphere(radius=1.0)
            
        if hasattr(mesh, 'geometry') and len(mesh.geometry) > 0:
            mesh = mesh.dump(concatenate=True)
            
        # 変換と保存
        transformed_mesh = engine.apply_transform(mesh)
        engine.export_mesh(transformed_mesh, args.output_mesh)
        
        # レポート出力
        result = engine.generate_report()
        params = result["physical_parameters"]
        print("\n" + "="*40)
        print(" SIMULATION SUCCESSFUL")
        print("="*40)
        print(f"Status: {result['status']}")
        print(f"Genetics: {result['genetic_profile']['hormone_profile']}")
        print("-" * 40)
        print(f"Resulting Metrics:")
        print(f"  Height  : {params['estimated_height']:.2f} (Ratio: {params['ratios_applied']['height_scaling']})")
        print(f"  Shoulder: {params['estimated_shoulder_width']:.2f} (Ratio: {params['ratios_applied']['shoulder_width']})")
        print(f"  Pelvis  : {params['estimated_pelvis_width']:.2f} (Ratio: {params['ratios_applied']['pelvis_width']})")
        print("-" * 40)
        print(f"Output saved to: {os.path.abspath(args.output_mesh)}")
        print("="*40 + "\n")

    except Exception as e:
        logging.error(f"An unexpected error occurred: {e}")
        sys.exit(1)

if __name__ == "__main__":
    main()