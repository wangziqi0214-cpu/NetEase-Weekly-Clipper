# Helper wrapper to expose researcher functions to streamlit
import os
import sys

# Hack to find the src module because streamlit runs in its own logic
sys.path.append(os.path.join(os.path.dirname(__file__), '..', '..'))
from src.researcher.researcher import search_and_generate_gemini

def search_song_info(name, artist):
    return "已启用 Gemini AI Studio Search Grounding。"

def generate_ai_copy(name, artist, context=""):
    # Direct pass to AI Studio
    return search_and_generate_gemini(name, artist)
