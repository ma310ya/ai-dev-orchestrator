import cv2
import numpy as np
import gradio as gr
from PIL import Image, ImageEnhance, ImageFilter, ImageDraw

def apply_kawaii_filter(input_image, skin_smooth, sparkle_eye, cheek_val, lip_val, anime_val):
    if input_image is None:
        return None
    
    try:
        # 画像の読み込みと準備
        img = input_image.convert("RGB")
        w, h = img.size
        
        # 1. 美肌・トーンアップ (Bilateral Filter代替をPILとOpenCVの最小限利用で実装)
        img_np = np.array(img)
        if skin_smooth > 0:
            d = int(skin_smooth / 10) * 2 + 3
            img_np = cv2.bilateralFilter(img_np, d, skin_smooth, skin_smooth)
        
        res_pil = Image.fromarray(img_np)
        
        # 2. 幾何学的オーバーレイ (顔認識を使用せず位置を推定)
        draw = ImageDraw.Draw(res_pil, "RGBA")
        
        # チーク (中央上寄りから左右へ)
        if cheek_val > 0:
            opacity = int(255 * (cheek_val / 200))
            cheek_color = (255, 150, 180, opacity)
            # 左頬
            draw.ellipse([w*0.15, h*0.5, w*0.4, h*0.65], fill=cheek_color)
            # 右頬
            draw.ellipse([w*0.6, h*0.5, w*0.85, h*0.65], fill=cheek_color)
            
        # リップ (下部中央)
        if lip_val > 0:
            opacity = int(255 * (lip_val / 200))
            draw.ellipse([w*0.4, h*0.75, w*0.6, h*0.85], fill=(220, 50, 50, opacity))

        # 目元・キラキラ (上部左右にランダム配置)
        if sparkle_eye > 0:
            for _ in range(int(sparkle_eye / 5)):
                sx = np.random.randint(int(w*0.2), int(w*0.8))
                sy = np.random.randint(int(h*0.2), int(h*0.4))
                size = np.random.randint(5, 15)
                draw.text((sx, sy), "✨", fill=(255, 255, 255, 200))

        # 3. 全体補正とアニメ調フィルタ
        if anime_val > 0:
            img_anime = res_pil.filter(ImageFilter.CONTOUR)
            res_pil = Image.blend(res_pil, img_anime, anime_val / 200)

        # 最終輝度・彩度調整
        res_pil = ImageEnhance.Brightness(res_pil).enhance(1.1)
        res_pil = ImageEnhance.Color(res_pil).enhance(1.2)
        
        return res_pil

    except Exception as e:
        print(f"Filter Error: {e}")
        return input_image

with gr.Blocks(title="💖 Kawaii Transform 💖") as demo:
    gr.Markdown("# 🌸 究極・かわいいフィルター (Haar-less version) 🌸")
    with gr.Row():
        with gr.Column():
            input_img = gr.Image(label="写真", type="pil")
            skin_slider = gr.Slider(0, 100, value=30, label="美肌")
            sparkle_slider = gr.Slider(0, 100, value=50, label="キラキラ")
            cheek_slider = gr.Slider(0, 100, value=40, label="チーク")
            lip_slider = gr.Slider(0, 100, value=0, label="リップ")
            anime_slider = gr.Slider(0, 100, value=0, label="アニメ調")
            btn = gr.Button("変換開始")
        with gr.Column():
            output_img = gr.Image(label="結果")
    
    btn.click(apply_kawaii_filter, 
              inputs=[input_img, skin_slider, sparkle_slider, cheek_slider, lip_slider, anime_slider], 
              outputs=output_img)

if __name__ == "__main__":
    demo.launch(server_name="0.0.0.0", server_port=7860)