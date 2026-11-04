import requests
import polib
import ssl
import certifi
from django.core.management.base import BaseCommand
from django.conf import settings
from one.settings import BASE_DIR
import os

class Command(BaseCommand):
    help = 'Automatically translate missing entries in the Hindi .po file using the translation API'

    def __init__(self):
        super().__init__()
        self.api_base_url = os.getenv("OPENAI_API_BASE_URL", "https://openrouter.ai/api/v1")
        self.api_key = __import__("os").environ.get("OPENAI_API_KEY", "")  # REPLACE WITH YOUR KEY
        self.model = os.getenv("OPENAI_TRANSLATION_MODEL", "moonshotai/kimi-k2")
        self.session = self._create_ssl_session()
    
    def _create_ssl_session(self):
        """Create requests session with SSL configuration"""
        ssl_verify = os.getenv("OPENAI_SSL_VERIFY", "True").lower() in ("true", "1", "yes")
        debug_mode = getattr(settings, 'DEBUG', False)
        
        session = requests.Session()
        
        if not ssl_verify:
            # SSL verification explicitly disabled via environment variable
            session.verify = False
            self.stdout.write(self.style.WARNING("SSL verification is disabled for translation command"))
        else:
            try:
                # Try with certifi certificate bundle first
                session.verify = certifi.where()
            except Exception as ssl_error:
                if debug_mode:
                    # In debug mode, fallback to system certificates
                    self.stdout.write(self.style.WARNING(f"SSL verification with certifi failed: {ssl_error}. Trying system certificates..."))
                    session.verify = True  # Use system certificates
                else:
                    # In production, re-raise the error
                    raise ssl_error
        
        return session

    def add_arguments(self, parser):
        parser.add_argument(
            '--fuzzy-only',
            action='store_true',
            help='Only translate fuzzy entries',
        )
        parser.add_argument(
            '--empty-only',
            action='store_true',
            help='Only translate empty entries (default behavior)',
        )

    def translate_text(self, text, target_language="hi", source_language="en"):
        """
        Translate text using the API
        """
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json"
        }
        
        data = {
            "model": self.model,
            "messages": [
                {
                    "role": "system",
                    "content": "You are a professional translator. Translate the text accurately while preserving its meaning and context. Provide only the translated text without any explanation."
                },
                {
                    "role": "user",
                    "content": f"Translate the following text from {source_language} to {target_language}. Provide only the translated text without any explanation:\n\n{text}"
                }
            ],
            "temperature": 0.3,
            "max_tokens": 1000
        }
        
        try:
            response = self.session.post(
                f"{self.api_base_url}/chat/completions",
                headers=headers,
                json=data,
                timeout=30
            )
            
            if response.status_code == 200:
                result = response.json()
                translated_text = result["choices"][0]["message"]["content"].strip()
                return translated_text
            else:
                self.stdout.write(
                    self.style.ERROR(f"Translation API error: {response.status_code} Client Error: {response.reason} for url: {response.url}")
                )
                return None

        except requests.exceptions.SSLError as e:
            self.stdout.write(
                self.style.ERROR(f"Translation SSL error: {str(e)}")
            )
            # Try one more time without SSL verification as fallback
            try:
                fallback_session = requests.Session()
                fallback_session.verify = False
                response = fallback_session.post(f"{self.api_base_url}/chat/completions", headers=headers, json=data, timeout=30)
                
                if response.status_code == 200:
                    result = response.json()
                    translated_text = result["choices"][0]["message"]["content"].strip()
                    self.stdout.write(self.style.SUCCESS("SSL fallback successful - translation completed without SSL verification"))
                    return translated_text
                else:
                    self.stdout.write(
                        self.style.ERROR(f"SSL fallback failed with status: {response.status_code}")
                    )
                    return None
            except Exception as fallback_error:
                self.stdout.write(
                    self.style.ERROR(f"SSL fallback also failed: {str(fallback_error)}")
                )
                return None
                
        except requests.exceptions.RequestException as e:
            self.stdout.write(
                self.style.ERROR(f"Translation API request error: {str(e)}")
            )
            return None
        except Exception as e:
            self.stdout.write(
                self.style.ERROR(f"Unexpected error during translation: {str(e)}")
            )
            return None

    def handle(self, *args, **options):
        # Path to the Hindi .po file
        po_file_path = os.path.join(BASE_DIR, 'locale', 'hi', 'LC_MESSAGES', 'django.po')
        
        if not os.path.exists(po_file_path):
            self.stdout.write(
                self.style.ERROR(f"PO file not found at {po_file_path}")
            )
            return
            
        # Load the .po file
        po = polib.pofile(po_file_path)
        
        # Counters for tracking progress
        total_entries = len([e for e in po if e.msgid and not e.obsolete])
        fuzzy_count = len(po.fuzzy_entries())
        untranslated_count = len([e for e in po if e.msgid and not e.msgstr and not e.obsolete])
        
        self.stdout.write(
            self.style.SUCCESS(f"Found {total_entries} total entries")
        )
        self.stdout.write(
            self.style.SUCCESS(f"Found {fuzzy_count} fuzzy entries")
        )
        self.stdout.write(
            self.style.SUCCESS(f"Found {untranslated_count} untranslated entries")
        )
        
        # Determine which entries to translate
        entries_to_translate = []
        if options['fuzzy_only']:
            entries_to_translate = po.fuzzy_entries()
            self.stdout.write(
                self.style.SUCCESS("Translating only fuzzy entries")
            )
        else:
            # Default: translate empty entries
            entries_to_translate = [e for e in po if e.msgid and not e.msgstr and not e.obsolete]
            self.stdout.write(
                self.style.SUCCESS("Translating empty entries")
            )
        
        if not entries_to_translate:
            self.stdout.write(
                self.style.WARNING("No entries to translate")
            )
            return
            
        self.stdout.write(
            self.style.SUCCESS(f"Starting translation of {len(entries_to_translate)} entries...")
        )
        
        # Translate entries
        translated_count = 0
        failed_count = 0
        
        for i, entry in enumerate(entries_to_translate, 1):
            if not entry.msgid:
                continue
                
            # Skip if already translated
            if entry.msgstr:
                continue
                
            self.stdout.write(
                f"Translating {i}/{len(entries_to_translate)}: {entry.msgid[:50]}..."
            )
            
            # Translate the text
            translated = self.translate_text(entry.msgid)
            
            if translated:
                entry.msgstr = translated
                # Remove fuzzy flag if present
                if 'fuzzy' in entry.flags:
                    entry.flags.remove('fuzzy')
                translated_count += 1
                self.stdout.write(
                    self.style.SUCCESS(f"✓ Translated: {translated[:50]}...")
                )
            else:
                failed_count += 1
                self.stdout.write(
                    self.style.ERROR(f"✗ Failed to translate: {entry.msgid[:50]}...")
                )
        
        # Save the updated .po file
        po.save(po_file_path)
        self.stdout.write(
            self.style.SUCCESS(f"Saved updated PO file to {po_file_path}")
        )
        
        # Summary
        self.stdout.write(
            self.style.SUCCESS("Translation completed!")
        )
        self.stdout.write(
            self.style.SUCCESS(f"Successfully translated: {translated_count}")
        )
        self.stdout.write(
            self.style.SUCCESS(f"Failed translations: {failed_count}")
        )
        self.stdout.write(
            self.style.SUCCESS(f"Remaining untranslated: {len(entries_to_translate) - translated_count - failed_count}")
        )
        
        # Instructions for next steps
        self.stdout.write(
            self.style.NOTICE("To compile the translations, run:")
        )
        self.stdout.write(
            self.style.NOTICE("python manage.py compilemessages")
        )