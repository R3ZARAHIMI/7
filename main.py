import re
import asyncio
import aiohttp
import time
import base64
import json
import yaml
import os
import platform
import uuid
from urllib.parse import urlparse, parse_qs
from concurrent.futures import ThreadPoolExecutor

# Pyrogram imports
from pyrogram import Client
from pyrogram.errors import FloodWait, RPCError

# --- تنظیمات عمومی ---

# اطلاعات API تلگرام (از GitHub Secrets خوانده می‌شود)
API_ID = int(os.environ.get("API_ID"))
API_HASH = os.environ.get("API_HASH")

# نام فایل سشن برای Pyrogram (باید با نامی که در GitHub Secret ذخیره کرده‌اید مطابقت داشته باشد)
SESSION_NAME = "v2rayTrack"

# کانال‌هایی که باید اسکن شوند
CHANNELS = [
    "@SRCVPN",
    "@sezar_sec",
    "@Anty_Filter", # @SRCVPN دو بار تکرار شده بود، یکی را حذف کردم
    "@proxy_kafee",
    "@vpns"
]

# خروجی‌ها
OUTPUT_YAML = "Config-jo.yaml"  # خروجی به فرمت YAML برای Clash
OUTPUT_TXT = "Config_jo.txt"    # خروجی به فرمت متنی ساده

# الگوهای شناسایی کانفیگ‌ها
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
# PROXY = {
#     "hostname": "127.0.0.1",
#     "port": 10808,
#     "scheme": "socks5"
# }
# در این حالت، چون از User Client استفاده می‌شود و سشن به صورت فایل منتقل می‌شود،
# بهتر است پروکسی در اینجا فعال نباشد مگر اینکه مطمئن باشید نیاز است.
# اگر به پروکسی نیاز دارید (مثلاً برای دور زدن فیلترینگ تلگرام در خود GitHub Actions)،
# باید یک پروکسی واقعی و قابل دسترس از خارج داشته باشید و تنظیمات آن را اینجا قرار دهید.
# در غیر این صورت، این را None بگذارید یا کامنت کنید.
GLOBAL_PROXY_SETTINGS = None # اگر پروکسی خاصی نیاز نیست، این را None بگذارید.

# تنظیمات تست کانفیگ‌ها
TEST_SETTINGS = {
    "timeout": 10,
    "test_urls": [
        "https://httpbin.org/ip",
        "https://api.ipify.org?format=json",
        "https://ifconfig.me/ip"
    ],
    "max_workers": 20, # حداکثر تعداد کانفیگ‌هایی که همزمان تست می‌شوند
    "ping_count": 1    # تعداد پینگ برای هر سرور (1 یا 2 کافی است)
}

# --- کلاس ConfigTester برای تست و تجزیه کانفیگ‌ها ---
class ConfigTester:
    def __init__(self):
        self.working_configs = []
        self.failed_configs = []

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
            print(f"❌ خطا در تجزیه کانفیگ ({config_url[:50]}...): {str(e)}")
            return None

    def parse_vmess(self, vmess_url):
        try:
            encoded_data = vmess_url.replace('vmess://', '')
            # Padding برای decode base64
            padding = len(encoded_data) % 4
            if padding:
                encoded_data += '=' * (4 - padding)

            decoded_data = base64.b64decode(encoded_data).decode('utf-8')
            config = json.loads(decoded_data)

            clash_config = {
                'name': config.get('ps', f"vmess-{str(uuid.uuid4())[:8]}"),
                'type': 'vmess',
                'server': config.get('add'),
                'port': int(config.get('port', 443)),
                'uuid': config.get('id'),
                'alterId': int(config.get('aid', 0)),
                'cipher': config.get('scy', 'auto'),
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
            print(f"❌ خطا در تجزیه VMess ({vmess_url[:50]}...): {str(e)}")
            return None

    def parse_vless(self, vless_url):
        try:
            parsed = urlparse(vless_url)
            query = parse_qs(parsed.query)

            clash_config = {
                'name': parse_qs(parsed.fragment).get('', [f"vless-{str(uuid.uuid4())[:8]}"])[0],
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
            print(f"❌ خطا در تجزیه VLESS ({vless_url[:50]}...): {str(e)}")
            return None

    def parse_trojan(self, trojan_url):
        try:
            parsed = urlparse(trojan_url)
            query = parse_qs(parsed.query)

            return {
                'name': parsed.fragment or f"trojan-{str(uuid.uuid4())[:8]}",
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
            print(f"❌ خطا در تجزیه Trojan ({trojan_url[:50]}...): {str(e)}")
            return None

    def parse_shadowsocks(self, ss_url):
        try:
            if '@' in ss_url:
                method_password, server_info = ss_url.replace('ss://', '').split('@')
                padding = len(method_password) % 4
                if padding:
                    method_password += '=' * (4 - padding)

                method_password = base64.b64decode(method_password).decode('utf-8')
                method, password = method_password.split(':', 1)
                host, port = server_info.split(':')

                return {
                    'name': f"ss-{str(uuid.uuid4())[:8]}",
                    'type': 'ss',
                    'server': host,
                    'port': int(port),
                    'password': password,
                    'cipher': method,
                    'udp': True
                }
        except Exception as e:
            print(f"❌ خطا در تجزیه Shadowsocks ({ss_url[:50]}...): {str(e)}")
            return None

    def parse_hysteria(self, hysteria_url):
        try:
            parsed = urlparse(hysteria_url)
            query = parse_qs(parsed.query)

            return {
                'name': parsed.fragment or f"hysteria-{str(uuid.uuid4())[:8]}",
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
            print(f"❌ خطا در تجزیه Hysteria ({hysteria_url[:50]}...): {str(e)}")
            return None

    def parse_tuic(self, tuic_url):
        try:
            parsed = urlparse(tuic_url)
            query = parse_qs(parsed.query)

            return {
                'name': parsed.fragment or f"tuic-{str(uuid.uuid4())[:8]}",
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
            print(f"❌ خطا در تجزیه TUIC ({tuic_url[:50]}...): {str(e)}")
            return None

    async def ping_test(self, host):
        """تست پینگ"""
        try:
            cmd = ["ping", "-n", "1", host] if platform.system().lower() == "windows" else ["ping", "-c", "1", host]
            process = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            stdout, stderr = await process.communicate()

            if process.returncode == 0:
                output = stdout.decode()
                # برای ویندوز: "time=XXms"  برای لینوکس: "time=XX.X ms"
                time_match = re.search(r'time[=<](\d+(?:\.\d+)?)ms', output)
                if time_match:
                    return float(time_match.group(1))
            return False
        except Exception as e:
            # print(f"⚠️ خطای پینگ برای {host}: {e}") # برای لاگ‌های تمیزتر، این رو فعلاً خاموش نگه می‌داریم
            return False

    async def tcp_test(self, host, port):
        """تست اتصال TCP"""
        try:
            future = asyncio.open_connection(host, port)
            # با timeout مشخص، از گیر کردن در اتصال‌های طولانی جلوگیری می‌کنیم
            reader, writer = await asyncio.wait_for(future, timeout=TEST_SETTINGS['timeout'] / 2)
            writer.close()
            await writer.wait_closed()
            return True
        except Exception as e:
            # print(f"⚠️ خطای TCP برای {host}:{port}: {e}")
            return False

    async def test_config_connection(self, config_info, config_url):
        """تست اتصال کانفیگ"""
        if not config_info:
            return False, "نمی‌تواند کانفیگ را تجزیه کند"

        # تست پینگ (اختیاری، اگر فقط TCP/HTTP تست می‌کنید)
        ping_latency = await self.ping_test(config_info['server'])
        if ping_latency is False:
            return False, f"پینگ ناموفق یا خیلی زیاد ({config_info['server']})"

        # تست اتصال TCP
        tcp_result = await self.tcp_test(config_info['server'], config_info['port'])
        if not tcp_result:
            return False, f"اتصال TCP ناموفق ({config_info['server']}:{config_info['port']})"

        return True, f"موفق - پینگ: {ping_latency:.2f}ms" # نمایش پینگ با دقت دو رقم اعشار

    async def test_single_config(self, config_url):
        """تست یک کانفیگ و ثبت نتیجه"""
        try:
            config_info = self.parse_config(config_url)
            success, message = await self.test_config_connection(config_info, config_url)

            result = {
                'config': config_url,
                'info': config_info,
                'working': success,
                'message': message,
                'test_time': time.time()
            }

            if success:
                self.working_configs.append(result)
                name = config_info['name'] if config_info and 'name' in config_info else 'Unknown'
                print(f"✅ {name}: {message}")
            else:
                self.failed_configs.append(result)
                name = config_info['name'] if config_info and 'name' in config_info else 'Unknown'
                print(f"❌ {name}: {message}")

            return result

        except Exception as e:
            print(f"❌ خطا در تست کانفیگ ({config_url[:50]}...): {str(e)}")
            return None

    async def test_all_configs(self, configs):
        """تست همه کانفیگ‌ها با محدودیت همزمانی"""
        print(f"🧪 شروع تست {len(configs)} کانفیگ...")
        print("=" * 50)

        # از ThreadPoolExecutor برای کارهایی که CPU-bound هستند (مانند پینگ) استفاده می‌کنیم
        # و از asyncio.Semaphore برای محدود کردن تعداد تسک‌های همزمان
        semaphore = asyncio.Semaphore(TEST_SETTINGS['max_workers'])

        async def test_with_semaphore(config):
            async with semaphore:
                return await self.test_single_config(config)

        tasks = [test_with_semaphore(config) for config in configs]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        print("=" * 50)
        print(f"📊 نتیجه تست:")
        print(f"✅ کانفیگ‌های کاری: {len(self.working_configs)}")
        print(f"❌ کانفیگ‌های غیرفعال: {len(self.failed_configs)}")
        if (len(self.working_configs) + len(self.failed_configs)) > 0:
            success_rate = len(self.working_configs) / (len(self.working_configs) + len(self.failed_configs)) * 100
            print(f"📈 نرخ موفقیت: {success_rate:.1f}%")
        else:
            print(f"📈 نرخ موفقیت: 0.0% (هیچ کانفیگی برای تست وجود نداشت)")

        return self.working_configs

# --- کلاس V2RayExtractor برای تعامل با تلگرام ---
class V2RayExtractor:
    def __init__(self):
        self.found_configs = set()
        # مقداردهی Client برای User Client
        self.client = Client(
            SESSION_NAME,
            api_id=API_ID,
            api_hash=API_HASH,
            # اگر GLOBAL_PROXY_SETTINGS تعریف شده و None نیست، از آن استفاده کن
            **({"proxy": GLOBAL_PROXY_SETTINGS} if GLOBAL_PROXY_SETTINGS else {})
        )
        self.tester = ConfigTester()

    async def check_channel(self, channel):
        """بررسی یک کانال تلگرام برای استخراج کانفیگ"""
        try:
            print(f"🔍 در حال بررسی کانال {channel}...")
            # از get_chat_history برای گرفتن پیام‌ها استفاده می‌کنیم.
            # چون این یک User Client است، باید به تاریخچه دسترسی داشته باشد.
            async for message in self.client.get_chat_history(channel, limit=10): # limit را کمی بیشتر کردم
                if not message.text:
                    continue

                for pattern in V2RAY_PATTERNS:
                    matches = pattern.findall(message.text)
                    for config in matches:
                        if config not in self.found_configs:
                            self.found_configs.add(config)
                            print(f"✅ کانفیگ جدید یافت شد از {channel}: {config[:60]}...")

        except FloodWait as e:
            print(f"⏳ نیاز به انتظار {e.value} ثانیه (محدودیت تلگرام) برای کانال {channel}")
            await asyncio.sleep(e.value)
            await self.check_channel(channel) # دوباره امتحان کن
        except RPCError as e: # برای خطاهای خاص Pyrogram (مثلاً Peer Flood)
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
            print("2. SESSION_NAME در main.py دقیقاً با نام فایل سشن شما (بدون پسوند) مطابقت دارد.")
            print("3. API_ID و API_HASH در GitHub Secrets صحیح هستند.")
            # اگر به این بخش رسید، یعنی اتصال به تلگرام ناموفق بوده، پس ادامه کار معنی ندارد
            self.found_configs.clear() # برای اطمینان از اینکه configs خالی باشند.


    async def test_and_save_configs(self):
        """تست و ذخیره کانفیگ‌ها به هر دو فرمت YAML و TXT"""
        if not self.found_configs:
            print("⚠️ هیچ کانفیگ جدیدی یافت نشد یا خطا در استخراج کانفیگ‌ها.")
            # ایجاد فایل‌های خالی برای جلوگیری از خطای "No such file or directory" در Git
            open(OUTPUT_YAML, "w").close()
            open(OUTPUT_TXT, "w").close()
            print(f"فایل‌های خالی {OUTPUT_YAML} و {OUTPUT_TXT} ایجاد شدند.")
            return

        print(f"\n🚀 شروع تست کردن {len(self.found_configs)} کانفیگ...")
        working_configs = await self.tester.test_all_configs(list(self.found_configs))

        if working_configs:
            # ذخیره به فرمت YAML برای Clash
            clash_config = {
                'proxies': [],
                'proxy-groups': [
                    {
                        'name': '🚀 Auto Select',
                        'type': 'url-test',
                        'proxies': [],
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

            # ذخیره به فرمت متنی ساده
            raw_configs = []

            for config_result in working_configs:
                if config_result['info']:
                    clash_config['proxies'].append(config_result['info'])
                    clash_config['proxy-groups'][0]['proxies'].append(config_result['info']['name'])
                raw_configs.append(config_result['config'])

            # ذخیره فایل YAML
            try:
                with open(OUTPUT_YAML, "w", encoding="utf-8") as f:
                    yaml.dump(clash_config, f, allow_unicode=True, sort_keys=False)
                print(f"🎉 {len(working_configs)} کانفیگ کارکرده در {OUTPUT_YAML} ذخیره شد.")
            except Exception as e:
                print(f"❌ خطا در ذخیره فایل YAML: {str(e)}")

            # ذخیره فایل متنی
            try:
                with open(OUTPUT_TXT, "w", encoding="utf-8") as f:
                    f.write("\n".join(raw_configs))
                print(f"🎉 {len(working_configs)} کانفیگ کارکرده در {OUTPUT_TXT} ذخیره شد.")
            except Exception as e:
                print(f"❌ خطا در ذخیره فایل TXT: {str(e)}")

            # نمایش جزئیات کانفیگ‌های کاری
            print(f"\n📋 لیستی از کانفیگ‌های کاری (10 مورد اول):")
            for i, config_result in enumerate(working_configs[:10], 1):
                info = config_result['info']
                if info:
                    print(f"{i}. {info.get('name', 'N/A')} ({info.get('type', 'N/A')}) - {info.get('server', 'N/A')}:{info.get('port', 'N/A')}")
                else:
                    print(f"{i}. {config_result['config'][:50]}...")
        else:
            print("😞 هیچ کانفیگ کاری یافت نشد!")
            # اطمینان از ایجاد فایل‌های خالی حتی در صورت عدم یافتن کانفیگ
            open(OUTPUT_YAML, "w").close()
            open(OUTPUT_TXT, "w").close()
            print(f"فایل‌های خالی {OUTPUT_YAML} و {OUTPUT_TXT} ایجاد شدند.")


async def main():
    print("🚀 شروع استخراج و تست کانفیگ‌های V2Ray...")
    print("=" * 60)

    extractor = V2RayExtractor()

    await extractor.extract_configs() # این مرحله ممکن است `self.found_configs` را خالی کند اگر خطا رخ دهد

    # فقط در صورتی ادامه می‌دهیم که کانفیگ‌هایی برای تست وجود داشته باشد
    if extractor.found_configs:
        await extractor.test_and_save_configs()
    else:
        print("⚠️ مرحله تست و ذخیره نادیده گرفته شد، زیرا هیچ کانفیگی یافت نشد.")
        # اطمینان از ایجاد فایل‌های خالی برای گیت
        open(OUTPUT_YAML, "w").close()
        open(OUTPUT_TXT, "w").close()
        print(f"فایل‌های خالی {OUTPUT_YAML} و {OUTPUT_TXT} برای Git Actions ایجاد شدند.")


    print("=" * 60)
    print("✨ اتمام کار!")

if __name__ == "__main__":
    asyncio.run(main())