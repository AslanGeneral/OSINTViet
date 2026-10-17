import os
import json
import pandas as pd
from datasets import load_dataset
from typing import List, Dict

CACHE_FILE = "data/matraix_1000_sample.json"

class PersonaManager:
    def __init__(self, sample_size: int = 1000):
        self.sample_size = sample_size
        self.personas: List[Dict] = []

    def load_personas(self) -> List[Dict]:
        """Tải hoặc load từ cache 1,000 persona từ MatrAIx dataset"""
        if os.path.exists(CACHE_FILE):
            with open(CACHE_FILE, "r", encoding="utf-8") as f:
                self.personas = json.load(f)
            return self.personas

        print("[*] Downloading & Sampling MatrAIx Persona dataset...")
        # Sử dụng dataset MatrAIx Persona trên Hugging Face
        dataset = load_dataset("MatrAIx2026/Demo_Application_Data", split="train", streaming=True)
        
        sampled = []
        for row in dataset:
            # Rút trích các thuộc tính vi mô quan trọng cho bài toán kinh tế
            persona_profile = {
                "id": len(sampled) + 1,
                "demographics": row.get("demographics", {}),
                "income_bracket": row.get("income_bracket", "Medium"),
                "savings_rate": row.get("savings_rate", 0.15),
                "price_sensitivity": row.get("price_sensitivity", "High"),
                "behavioral_traits": row.get("behavioral_traits", {})
            }
            sampled.append(persona_profile)
            if len(sampled) >= self.sample_size:
                break
        
        os.makedirs(os.path.dirname(CACHE_FILE), exist_ok=True)
        with open(CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump(sampled, f, indent=2)

        self.personas = sampled
        return self.personas