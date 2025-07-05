import re
import asyncio
import base64
import json
import yaml
import os
import uuid
from urllib.parse import urlparse, parse_qs

# Pyrogram imports
from pyrogram import Client
from pyrogram.errors import FloodWait, RPCError

# --- تنظیمات عمومی ---

# اطلاعات API تلگرام (از GitHub Secrets خوانده می‌شود)
API_ID = int(os.environ.get("API_ID"))
API_HASH = os.environ.get("API_HASH")

# نام فایل سشن برای Pyrogram (باید با نامی که در GitHub Secret ذخیره کرده‌اید مطابقت داشته باشد)
# این نام باید دقیقاً با نام فایل سشن محلی شما (مثلاً my_account.session) مطابقت داشته باشد، اما بدون پسوند .session
SESSION_NAME = "my_account"

# کانال‌هایی که باید اسکن شوند
CHANNELS = [
    # "@SRCVPN",
    # "@sezar_sec",
    # "@Anty_Filter",
    # "@proxy_kafee",
    "@vpns"
]

# خروجی‌ها
OUTPUT_YAML = "Config-jo.yaml"  # خروجی به فرمت YAML برای Clash
OUTPUT_TXT = "Config_jo.txt"    # خروجی به فرمت متنی ساده

# الگوهای شناسایی کانفیگ‌ها (این‌ها کانفیگ‌های URL را شناسایی می‌کنند)
V2RAY_PATTERNS = [
    re.compile(r"(vless://[^\s]+)"),
    re.compile(r"(vmess://[^\s]+)"),
    re.compile(r"(trojan://[^\s]+)"),
    re.compile(r"(ss://[^\s]+)"),
    re.compile(r"(hy2://[^\s]+)"),
    re.compile(r"(hysteria://[^\s]+)"),
    re.compile(r"(tuic://[^\s]+)")
]

# تنظیمات پروکسی (اختیاری) - برای GitHub Actions معمولاً غیرفعال است
GLOBAL_PROXY_SETTINGS = None

# --- کلاس V2RayExtractor برای تعامل با تلگرام و ذخیره‌سازی ---
class V2RayExtractor:
    def __init__(self):
        self.found_configs = set() # مجموعه ای از کانفیگ‌های URL پیدا شده (رشته‌های خام)
        self.parsed_clash_configs = [] # لیست کانفیگ‌ها بعد از تجزیه برای فرمت Clash (دیکشنری‌ها)

        # مقداردهی Client برای User Client
        self.client = Client(
            SESSION_NAME,
            api_id=API_ID,
            api_hash=API_HASH,
            # bot_token=BOT_TOKEN, # این خط باید حذف یا کامنت شود زیرا ما از User Client استفاده می‌کنیم
            **({"proxy": GLOBAL_PROXY_SETTINGS} if GLOBAL_PROXY_SETTINGS else {})
        )

    # توابع تجزیه کانفیگ‌ها
    def parse_config(self, config_url):
        """تجزیه و تحلیل کانفیگ برای استخراج اطلاعات اتصال برای Clash"""
        try:
            if config_url.startswith('vmess://'):
                return self.parse_vmess(config_url)
            elif config_url.startswith('vless://'):
                return self.parse_vless(config_url)
            elif config_url.startswith('trojan://'):
                return self.parse_trojan(config_url)
            elif config_url.startswith('ss://'):
                return self.parse_shadowsocks(config_url)
            elif config_url.startswith('hy2://') or config_url.startswith('hysteria://'):
                return self.parse_hysteria(config_url)
            elif config_url.startswith('tuic://'):
                return self.parse_tuic(config_url)
            else:
                return None
        except Exception as e:
            # print(f"❌ خطا در تجزیه کانفیگ ({config_url[:50]}...): {str(e)}") # برای کاهش لاگ‌ها
            return None

    # تابع کمکی برای تولید نام منحصر به فرد
    def _generate_unique_name(self, original_name, prefix="config"):
        # حذف کاراکترهای خاصی که Clash ممکن است با آنها مشکل داشته باشد
        cleaned_name = re.sub(r'[^\w\s\-\_]', '', original_name)
        # جایگزینی فضا با آندرلاین
        cleaned_name = cleaned_name.replace(' ', '_')
        # حذف خط تیره یا آندرلاین اضافی از ابتدا/انتها
        cleaned_name = cleaned_name.strip('_-')
        
        # اضافه کردن یک UUID کوتاه برای تضمین منحصر به فرد بودن
        # اگر نام اصلی خالی بود، از پیشوند + UUID استفاده کن
        final_name = f"{cleaned_name}-{str(uuid.uuid4())[:8]}" if cleaned_name else f"{prefix}-{str(uuid.uuid4())[:8]}"
        
        # محدود کردن طول نام نهایی
        return final_name[:50]


    def parse_vmess(self, vmess_url):
        try:
            encoded_data = vmess_url.replace('vmess://', '')
            padding = len(encoded_data) % 4
            if padding:
                encoded_data += '=' * (4 - padding)

            decoded_data = base64.b64decode(encoded_data).decode('utf-8')
            config = json.loads(decoded_data)

            # --- اصلاح برای نام منحصر به فرد ---
            original_name = config.get('ps', '')
            unique_name = self._generate_unique_name(original_name, "vmess")

            # --- اصلاح برای رفع 'vmess unsupported secu' ---
            vmess_cipher = config.get('scy', 'auto')
            clash_cipher = vmess_cipher
            supported_clash_ciphers = ['aes-128-gcm', 'chacha20-poly1305', 'aes-256-gcm']

            if vmess_cipher.lower() == 'none' or vmess_cipher.lower() == 'auto':
                clash_cipher = 'aes-128-gcm'
            elif vmess_cipher not in supported_clash_ciphers:
                clash_cipher = 'aes-128-gcm'

            clash_config = {
                'name': unique_name,
                'type': 'vmess',
                'server': config.get('add'),
                'port': int(config.get('port', 443)),
                'uuid': config.get('id'),
                'alterId': int(config.get('aid', 0)),
                'cipher': clash_cipher,
                'tls': config.get('tls') == 'tls',
                'skip-cert-verify': False,
                'network': config.get('net', 'tcp'),
                'udp': True
            }

            if clash_config['network'] == 'ws':
                clash_config['ws-opts'] = {
                    'path': config.get('path', '/'),
                    'headers': {'Host': config.get('host', '')} if config.get('host') else {}
                }
            if clash_config['network'] == 'h2':
                clash_config['h2-opts'] = {
                    'path': config.get('path', '/'),
                    'host': [config.get('host', '')] if config.get('host') else []
                }
            if clash_config['network'] == 'grpc':
                clash_config['grpc-opts'] = {
                    'grpc-service-name': config.get('path', '')
                }
            return clash_config
        except Exception as e:
            return None

    def parse_vless(self, vless_url):
        try:
            parsed = urlparse(vless_url)
            query = parse_qs(parsed.query)
            
            # --- اصلاح برای نام منحصر به فرد ---
            original_name = parse_qs(parsed.fragment).get('', [''])[0]
            if not original_name:
                 original_name = query.get('ps', [''])[0] # گاهی اوقات نام در query param 'ps' هست
            unique_name = self._generate_unique_name(original_name, "vless")
            # --- پایان اصلاح ---

            clash_config = {
                'name': unique_name,
                'type': 'vless',
                'server': parsed.hostname,
                'port': parsed.port or 443,
                'uuid': parsed.username,
                'tls': query.get('security', [''])[0] == 'tls',
                'skip-cert-verify': False,
                'udp': True,
                'network': query.get('type', ['tcp'])[0],
                'servername': query.get('sni', [''])[0]
            }

            if 'flow' in query:
                clash_config['flow'] = query['flow'][0]
            if clash_config['network'] == 'ws':
                clash_config['ws-opts'] = {
                    'path': query.get('path', ['/'])[0],
                    'headers': {'Host': query.get('host', [''])[0]} if query.get('host') else {}
                }
            if clash_config['network'] == 'h2':
                clash_config['h2-opts'] = {
                    'path': query.get('path', ['/'])[0],
                    'host': [query.get('host', [''])[0]] if query.get('host') else []
                }
            if clash_config['network'] == 'grpc':
                clash_config['grpc-opts'] = {
                    'grpc-service-name': query.get('serviceName', [''])[0]
                }
            return clash_config
        except Exception as e:
            return None

    def parse_trojan(self, trojan_url):
        try:
            parsed = urlparse(trojan_url)
            query = parse_qs(parsed.query)
            
            # --- اصلاح برای نام منحصر به فرد ---
            original_name = parsed.fragment or ''
            if not original_name:
                 original_name = query.get('ps', [''])[0]
            unique_name = self._generate_unique_name(original_name, "trojan")
            # --- پایان اصلاح ---

            return {
                'name': unique_name,
                'type': 'trojan',
                'server': parsed.hostname,
                'port': parsed.port or 443,
                'password': parsed.username,
                'sni': query.get('sni', [''])[0],
                'skip-cert-verify': False,
                'udp': True,
                'network': query.get('type', ['tcp'])[0],
                'alpn': query.get('alpn', [''])[0].split(',') if query.get('alpn') else None
            }
        except Exception as e:
            return None

    def parse_shadowsocks(self, ss_url):
        try:
            if '@' in ss_url:
                method_password, server_info = ss_url.replace('ss://', '').split('@')
                padding = len(method_password) % 4
                if padding:
                    method_password += '=' * (4 - padding)

                decoded_method_password = base64.b64decode(method_password).decode('utf-8')
                method, password = decoded_method_password.split(':', 1)
                
                host_port_fragment = server_info.split('#', 1)
                host_port = host_port_fragment[0]
                
                # --- اصلاح برای نام منحصر به فرد ---
                original_name = host_port_fragment[1] if len(host_port_fragment) > 1 else ''
                if not original_name:
                    original_name = host_port # Fallback to server:port if no name/fragment
                unique_name = self._generate_unique_name(original_name, "ss")
                # --- پایان اصلاح ---

                host, port = host_port.split(':')

                return {
                    'name': unique_name,
                    'type': 'ss',
                    'server': host,
                    'port': int(port),
                    'password': password,
                    'cipher': method,
                    'udp': True
                }
        except Exception as e:
            return None

    def parse_hysteria(self, hysteria_url):
        try:
            parsed = urlparse(hysteria_url)
            query = parse_qs(parsed.query)
            
            # --- اصلاح برای نام منحصر به فرد ---
            original_name = parsed.fragment or ''
            if not original_name:
                 original_name = query.get('ps', [''])[0]
            unique_name = self._generate_unique_name(original_name, "hysteria")
            # --- پایان اصلاح ---

            return {
                'name': unique_name,
                'type': 'hysteria',
                'server': parsed.hostname,
                'port': parsed.port or 443,
                'auth_str': parsed.username,
                'obfs': query.get('obfsParam', [''])[0],
                'protocol': query.get('protocol', ['udp'])[0],
                'up_mbps': int(query.get('upmbps', ['10'])[0]),
                'down_mbps': int(query.get('downmbps', ['50'])[0]),
                'sni': query.get('peer', [''])[0],
                'skip-cert-verify': False,
                'alpn': query.get('alpn', [''])[0].split(',') if query.get('alpn') else None
            }
        except Exception as e:
            return None

    def parse_tuic(self, tuic_url):
        try:
            parsed = urlparse(tuic_url)
            query = parse_qs(parsed.query)
            
            # --- اصلاح برای نام منحصر به فرد ---
            original_name = parsed.fragment or ''
            if not original_name:
                 original_name = query.get('ps', [''])[0]
            unique_name = self._generate_unique_name(original_name, "tuic")
            # --- پایان اصلاح ---

            return {
                'name': unique_name,
                'type': 'tuic',
                'server': parsed.hostname,
                'port': parsed.port or 443,
                'uuid': parsed.username,
                'password': query.get('password', [''])[0],
                'sni': query.get('sni', [''])[0],
                'udp-relay-mode': query.get('udp-relay-mode', ['native'])[0],
                'skip-cert-verify': False,
                'alpn': query.get('alpn', [''])[0].split(',') if query.get('alpn') else None
            }
        except Exception as e:
            return None

    async def check_channel(self, channel):
        """بررسی یک کانال تلگرام برای استخراج کانفیگ"""
        try:
            print(f"🔍 در حال بررسی کانال {channel}...")
            async for message in self.client.get_chat_history(channel, limit=100):
                if not message.text:
                    continue

                for pattern in V2RAY_PATTERNS:
                    matches = pattern.findall(message.text)
                    for config_url in matches:
                        if config_url not in self.found_configs:
                            self.found_configs.add(config_url)
                            print(f"✅ کانفیگ جدید یافت شد از {channel}: {config_url[:60]}...")
                            # --- پرینت دیباگ: URL خام استخراج شده ---
                            print(f"DEBUG: URL خام استخراج شده از {channel}: {config_url}")
                            
                            clash_format = self.parse_config(config_url)
                            if clash_format:
                                self.parsed_clash_configs.append({
                                    'original_url': config_url, # ذخیره URL خام
                                    'clash_info': clash_format
                                })
                                # --- پرینت دیباگ: اطلاعات بعد از تجزیه ---
                                print(f"DEBUG: اطلاعات تجزیه شده (نوع): {clash_format.get('type')}, نام: {clash_format.get('name')}")

        except FloodWait as e:
            print(f"⏳ نیاز به انتظار {e.value} ثانیه (محدودیت تلگرام) برای کانال {channel}")
            await asyncio.sleep(e.value)
            await self.check_channel(channel)
        except RPCError as e:
            print(f"❌ خطای RPC در کانال {channel}: {e.MESSAGE} (کد: {e.CODE})")
        except Exception as e:
            print(f"❌ خطای عمومی در کانال {channel}: {str(e)}")


    async def extract_configs(self):
        """اتصال به تلگرام و استخراج کانفیگ‌ها از تمام کانال‌ها"""
        print("Connecting to Telegram as User Client...")
        try:
            async with self.client:
                print("Successfully connected to Telegram.")
                tasks = [self.check_channel(channel) for channel in CHANNELS]
                await asyncio.gather(*tasks)
        except Exception as e:
            print(f"🔴 خطای اتصال به تلگرام یا استخراج کانفیگ: {str(e)}")
            print("لطفاً مطمئن شوید:")
            print("1. Secret PYROGRAM_SESSION در GitHub Secrets به درستی و کامل Base64 شده است.")
            print(f"2. SESSION_NAME در main.py (فعلاً '{SESSION_NAME}') دقیقاً با نام فایل سشن شما (بدون پسوند) مطابقت دارد.")
            print("3. API_ID و API_HASH در GitHub Secrets صحیح هستند.")
            self.found_configs.clear()
            self.parsed_clash_configs.clear()


    async def save_configs(self):
        """ذخیره تمام کانفیگ‌های پیدا شده به هر دو فرمت YAML و TXT (بدون تست)"""
        if not self.found_configs:
            print("⚠️ هیچ کانفیگی یافت نشد یا خطا در استخراج کانفیگ‌ها.")
            open(OUTPUT_YAML, "w").close()
            open(OUTPUT_TXT, "w").close()
            print(f"فایل‌های خالی {OUTPUT_YAML} و {OUTPUT_TXT} ایجاد شدند.")
            return

        print(f"\n💾 شروع ذخیره {len(self.found_configs)} کانفیگ پیدا شده...")

        # --- ذخیره به فرمت YAML برای Clash ---
        clash_proxies_list = [item['clash_info'] for item in self.parsed_clash_configs]
        clash_proxy_names = [item['clash_info']['name'] for item in self.parsed_clash_configs if 'name' in item['clash_info']]

        clash_config_output = {
            'proxies': clash_proxies_list,
            'proxy-groups': [
                {
                    'name': '🚀 Auto Select',
                    'type': 'url-test',
                    'proxies': clash_proxy_names,
                    'url': 'http://www.gstatic.com/generate_204',
                    'interval': 300
                },
                {
                    'name': '🔮 Proxy',
                    'type': 'select',
                    'proxies': ['🚀 Auto Select', 'DIRECT']
                },
                {
                    'name': '🎯 Domestic',
                    'type': 'select',
                    'proxies': ['DIRECT']
                }
            ],
            'rules': [
                'DOMAIN-SUFFIX,ir,🎯 Domestic',
                'GEOIP,IR,🎯 Domestic',
                'MATCH,🔮 Proxy'
            ]
        }

        try:
            with open(OUTPUT_YAML, "w", encoding="utf-8") as f:
                yaml.dump(clash_config_output, f, allow_unicode=True, sort_keys=False)
            print(f"🎉 {len(clash_proxies_list)} کانفیگ در {OUTPUT_YAML} ذخیره شد.")
        except Exception as e:
            print(f"❌ خطا در ذخیره فایل YAML: {str(e)}")

        # --- ذخیره به فرمت متنی ساده (با استفاده از original_url) ---
        raw_configs_output_final = []
        for item in self.parsed_clash_configs:
            original_url = item['original_url']
            raw_configs_output_final.append(original_url) # اضافه کردن URL اصلی و خام پیدا شده از تلگرام

        try:
            with open(OUTPUT_TXT, "w", encoding="utf-8") as f:
                f.write("\n".join(raw_configs_output_final))
            print(f"🎉 {len(raw_configs_output_final)} کانفیگ در {OUTPUT_TXT} ذخیره شد.")
        except Exception as e:
            print(f"❌ خطا در ذخیره فایل TXT: {str(e)}")

        # --- نمایش جزئیات کانفیگ‌های پیدا شده ---
        print(f"\n📋 لیستی از کانفیگ‌های پیدا شده (10 مورد اول):")
        for i, item in enumerate(self.parsed_clash_configs[:10], 1):
            config_info = item['clash_info']
            if config_info:
                print(f"{i}. نام: {config_info.get('name', 'N/A')}, نوع: ({config_info.get('type', 'N/A')}), سرور: {config_info.get('server', 'N/A')}:{config_info.get('port', 'N/A')}")
            else:
                print(f"{i}. (کانفیگ نامعتبر)")


async def main():
    print("🚀 شروع استخراج کانفیگ‌های V2Ray...")
    print("=" * 60)

    extractor = V2RayExtractor()

    await extractor.extract_configs()

    await extractor.save_configs()

    print("=" * 60)
    print("✨ اتمام کار!")

if __name__ == "__main__":
    asyncio.run(main())