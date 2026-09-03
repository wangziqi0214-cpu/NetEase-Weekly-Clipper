import numpy as np
from PIL import Image
import os

def generate_noise_texture(width=1080, height=1440, output_path="assets/noise.png"):
    print(f"Generating noise texture: {width}x{height}")

    # Generate random gaussian noise
    # Mean 128, Std 50
    noise = np.random.normal(128, 50, (height, width))
    noise = np.clip(noise, 0, 255).astype(np.uint8)

    img = Image.fromarray(noise, mode='L')

    # Ensure directory exists
    os.makedirs(os.path.dirname(output_path), exist_ok=True)

    img.save(output_path)
    print(f"Saved to {output_path}")

if __name__ == "__main__":
    generate_noise_texture()
