import json
import os
import ssl
import certifi
import requests
from django.conf import settings
from typing import Dict, List, Optional


class TranslationService:
    """Service to handle automatic translations using OpenAI API"""

    def __init__(self):
        self.base_url = settings.OPENAI_API_BASE_URL
        self.api_key = settings.OPENAI_API_KEY
        self.model = settings.OPENAI_TRANSLATION_MODEL
        # Create language map from settings
        self.language_map = dict(settings.LANGUAGES)
        
        # Configure SSL settings similar to FPO AI processor
        self.session = self._create_ssl_session()
    
    def _create_ssl_session(self):
        """Create requests session with SSL configuration"""
        ssl_verify = os.getenv("OPENAI_SSL_VERIFY", "True").lower() in ("true", "1", "yes")
        debug_mode = getattr(settings, 'DEBUG', False)
        
        session = requests.Session()
        
        if not ssl_verify:
            # SSL verification explicitly disabled via environment variable
            session.verify = False
            print("Warning: SSL verification is disabled for TranslationService")
        else:
            try:
                # Try with certifi certificate bundle first
                session.verify = certifi.where()
            except Exception as ssl_error:
                if debug_mode:
                    # In debug mode, fallback to system certificates
                    print(f"SSL verification with certifi failed: {ssl_error}. Trying system certificates...")
                    session.verify = True  # Use system certificates
                else:
                    # In production, re-raise the error
                    raise ssl_error
        
        return session

    def translate_text(self, text: str, target_language: str, source_language: str = "en") -> Optional[str]:
        """
        Translate text from source language to target language using OpenAI API

        Args:
            text: Text to translate
            target_language: Target language code (e.g., 'hi' for Hindi)
            source_language: Source language code (default: 'en' for English)

        Returns:
            Translated text or None if translation fails
        """
        target_language_name = self.language_map.get(target_language, target_language)
        source_language_name = self.language_map.get(source_language, source_language)

        prompt = f"Translate the following text from {source_language_name} to {target_language_name}. Provide only the translated text without any explanation:\n\n{text}"

        headers = {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}

        data = {
            "model": self.model,
            "messages": [
                {
                    "role": "system",
                    "content": "You are a professional translator. Translate the text accurately while preserving its meaning and context.",
                },
                {"role": "user", "content": prompt},
            ],
            "temperature": 0.3,
            "max_tokens": 1000,
        }

        try:
            response = self.session.post(f"{self.base_url}/chat/completions", headers=headers, json=data, timeout=30)
            
            if response.status_code == 200:
                result = response.json()
                translated_text = result["choices"][0]["message"]["content"].strip()
                return translated_text
            else:
                print(f"Translation API error: {response.status_code} Client Error: {response.reason} for url: {response.url}")
                # For client errors (4xx), return None to indicate failure
                # For server errors (5xx), we might want to retry or handle differently
                return None
                
        except requests.exceptions.SSLError as e:
            print(f"Translation SSL error: {str(e)}")
            # Try one more time without SSL verification as fallback
            try:
                fallback_session = requests.Session()
                fallback_session.verify = False
                response = fallback_session.post(f"{self.base_url}/chat/completions", headers=headers, json=data, timeout=30)
                
                if response.status_code == 200:
                    result = response.json()
                    translated_text = result["choices"][0]["message"]["content"].strip()
                    print("SSL fallback successful - translation completed without SSL verification")
                    return translated_text
                else:
                    print(f"SSL fallback failed with status: {response.status_code}")
                    return None
            except Exception as fallback_error:
                print(f"SSL fallback also failed: {str(fallback_error)}")
                return None
                
        except requests.exceptions.RequestException as e:
            print(f"Translation API request error: {str(e)}")
            return None
        except Exception as e:
            print(f"Unexpected error during translation: {str(e)}")
            return None

    def translate_batch(self, text: str, target_languages: List[str], source_language: str = "en") -> Dict[str, str]:
        """
        Translate text to multiple languages

        Args:
            text: Text to translate
            target_languages: List of target language codes
            source_language: Source language code (default: 'en')

        Returns:
            Dictionary mapping language codes to translated texts
        """
        translations = {}

        for lang_code in target_languages:
            if lang_code != source_language:
                translated = self.translate_text(text, lang_code, source_language)
                if translated:
                    translations[lang_code] = translated

        return translations
