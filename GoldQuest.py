import logging
import cv2
import numpy as np
import subprocess
import time
import argparse
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
import os
import msvcrt
import random
import requests
import threading
import colorama
from colorama import Fore, Style

# coloramaの初期化
colorama.init(autoreset=True)

ADB_PATH = r"C:\adb\adb.exe"
WEBHOOK_URL = "https://discord.com/api/webhooks/1351841313439612938/6VK9MD3hAGe0Yzuhp03Lu8mLQGR7b6YjoVUz3h8LIP9OvIhTPBkpFY8hR14BfVjv1P7l"

logging.getLogger().setLevel(logging.WARNING)

# デバイス処理中にクラッシュしても他のデバイスに影響しないようにするためのロック
device_locks = {}

# デバイスごとの色を管理するディクショナリ
device_colors = {}
available_colors = [Fore.RED, Fore.GREEN, Fore.YELLOW, Fore.BLUE, Fore.MAGENTA, Fore.CYAN, Fore.WHITE]

# 画面停滞検出用
TIMEOUT_SECONDS = 15  # 同じ画面で15秒以上停滞したとみなす時間

def get_device_color(device_id):
    """デバイスIDに基づいて一貫した色を返す"""
    if device_id not in device_colors:
        # デバイスに色がまだ割り当てられていない場合、新しい色を割り当てる
        color_index = len(device_colors) % len(available_colors)
        device_colors[device_id] = available_colors[color_index]
    return device_colors[device_id]

def colored_print(device_id, message):
    """デバイスIDに基づいて色付きのログを出力する"""
    color = get_device_color(device_id)
    print(f"{color}[デバイス {device_id}] {message}{Style.RESET_ALL}")

# 通常のprint関数（デバイスIDなし）
def normal_print(message):
    print(message)

def single_instance(lock_file_path):
    try:
        lock_file = open(lock_file_path, 'w')
        msvcrt.locking(lock_file.fileno(), msvcrt.LK_NBLCK, 1)
        return lock_file
    except IOError:
        print("既に別のインスタンスが起動しています。")
        sys.exit(0)


lock = single_instance(os.path.join(os.path.dirname(__file__), "Main.lock"))


def try_connect(port):
    target_ip = f"127.0.0.1:{port}"
    try:
        result = subprocess.run([ADB_PATH, "connect", target_ip], capture_output=True, text=True)
        if "connected to" in result.stdout or "already connected" in result.stdout:
            print(f"{target_ip} への接続に成功しました。")
            return target_ip
    except Exception as e:
        pass
    return None


def auto_connect_ports(start_port=5555, end_port=5700):
    connected_ips = []
    with ThreadPoolExecutor(max_workers=50) as executor:
        futures = {executor.submit(try_connect, port): port for port in range(start_port, end_port + 1)}
        for future in as_completed(futures):
            result = future.result()
            if result:
                connected_ips.append(result)
    return connected_ips


def adb_auto_connect(target_ip="127.0.0.1:5655"):
    try:
        result = subprocess.run([ADB_PATH, "devices"], capture_output=True, text=True)
        lines = result.stdout.splitlines()
        devices = [line for line in lines[1:] if "\tdevice" in line]
        if devices:
            print("ADBデバイスが既に接続されています。")
        else:
            print("ADBデバイスが見つかりません。自動接続を試みます。")
            connect_result = subprocess.run([ADB_PATH, "connect", target_ip], capture_output=True, text=True)
            if "connected to" in connect_result.stdout or "already connected" in connect_result.stdout:
                print(f"ADBに正常に接続しました: {target_ip}")
            else:
                print("ADBへの接続に失敗しました。接続状況を確認してください。")
    except Exception as e:
        print("ADB接続確認中にエラーが発生しました:", e)


def get_adb_devices():
    try:
        result = subprocess.run([ADB_PATH, "devices"], capture_output=True, text=True)
        lines = result.stdout.splitlines()
        devices = [line.split()[0] for line in lines[1:] if "\tdevice" in line]
        if devices:
            print("ADB 接続デバイス:", devices)
        else:
            print("ADB に接続されたデバイスが見つかりません。")
        return devices
    except Exception as e:
        print("ADB デバイス取得中にエラーが発生しました:", e)
        return []


def capture_screen(device_id=None):
    cmd = [ADB_PATH]
    if device_id:
        cmd += ["-s", device_id]
    cmd += ["exec-out", "screencap", "-p"]
    result = subprocess.run(cmd, capture_output=True)
    if result.returncode != 0:
        print("adb でのスクリーンショット取得に失敗しました。")
        return None
    img_array = np.frombuffer(result.stdout, np.uint8)
    img = cv2.imdecode(img_array, cv2.IMREAD_COLOR)
    if img is not None:
        cv2.imwrite("debug_capture.png", img)
    return img


def match_template(screen, template, threshold=0.85):
    res = cv2.matchTemplate(screen, template, cv2.TM_CCOEFF_NORMED)
    loc = np.where(res >= threshold)
    if len(loc[0]) > 0:
        _, max_val, _, max_loc = cv2.minMaxLoc(res)
        if max_val >= threshold:
            return True, max_loc
    return False, None


def tap(x, y, device_id=None):
    cmd = [ADB_PATH]
    if device_id:
        cmd += ["-s", device_id]
    cmd += ["shell", "input", "tap", str(x), str(y)]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        logging.error("デバイス %s のタップ失敗: (%d, %d)", device_id, x, y)
    else:
        logging.info("タップ成功: (%d, %d)", x, y)

def swipe(x1, y1, x2, y2, duration=300, device_id=None):
    cmd = [ADB_PATH]
    if device_id:
        cmd += ["-s", device_id]
    cmd += ["shell", "input", "swipe", str(x1), str(y1), str(x2), str(y2), str(duration)]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        logging.error("デバイス %s のスワイプ失敗: (%d, %d) -> (%d, %d)", device_id, x1, y1, x2, y2)
    else:
        logging.info("スワイプ成功: (%d, %d) -> (%d, %d)", x1, y1, x2, y2)

def send_discord_webhook(message):
    data = {"content": message}
    try:
        response = requests.post(WEBHOOK_URL, json=data)
        if response.status_code not in (200, 204):
            print("Webhook送信に失敗しました。ステータスコード:", response.status_code)
    except Exception as e:
        print("Webhook送信中にエラーが発生しました:", e)

def check_all_templates(device_id, screen, templates):
    """全てのテンプレート画像をチェックし、マッチしたテンプレートとその位置を返す"""
    template_matches = {}
    
    # 各テンプレートをチェック
    for template_name, template in templates.items():
        if template is None:
            continue
        
        found, pos = match_template(screen, template, threshold=0.8)
        if found:
            center_x = pos[0] + template.shape[1] // 2
            center_y = pos[1] + template.shape[0] // 2
            template_matches[template_name] = (center_x, center_y)
    
    if template_matches:
        colored_print(device_id, f"画面停滞を検出。マッチしたテンプレート: {', '.join(template_matches.keys())}")
    else:
        colored_print(device_id, "画面停滞を検出しましたが、マッチするテンプレートが見つかりませんでした。")
        
    return template_matches

def check_screen_stagnation(device_id, last_screen, last_change_time, templates):
    """画面が一定時間変化していないかチェックし、停滞していれば適切な処理を行う"""
    current_time = time.time()
    if current_time - last_change_time >= TIMEOUT_SECONDS:
        colored_print(device_id, f"画面が{TIMEOUT_SECONDS}秒以上変化していません。全テンプレートをチェックします。")
        
        # 最新の画面をキャプチャ
        current_screen = capture_screen(device_id)
        if current_screen is None:
            return None, last_change_time
        
        # 全テンプレートをチェック
        template_matches = check_all_templates(device_id, current_screen, templates)
        return template_matches, current_time
    
    return None, last_change_time

def is_screen_changed(prev_screen, current_screen, threshold=0.98):
    """2つの画面が異なるかどうかを判断する"""
    if prev_screen is None or current_screen is None:
        return True
    
    # 画像のサイズが異なる場合は変化したとみなす
    if prev_screen.shape != current_screen.shape:
        return True
    
    # 画像の類似度を計算
    result = cv2.matchTemplate(prev_screen, current_screen, cv2.TM_CCOEFF_NORMED)
    similarity = np.max(result)
    
    # 類似度が閾値未満なら画面が変化したとみなす
    return similarity < threshold

def process_device(device_id, templates):
    """個々のデバイスに対する処理を行う関数"""
    colored_print(device_id, "処理を開始します。")
    
    try:
        template_quest1 = templates["quest1"]
        template_sukeet = templates["sukeet"]
        template_sukeet2 = templates["sukeet2"]
        template_go = templates["go"]
        template_menu = templates["menu"]
        template_Boss_Hp0 = templates["Boss_Hp0"]
        template_clear_ok = templates["clear_ok"]
        template_result_ok = templates["result_ok"]
        template_special_housyu = templates["special_housyu"]
        template_super_rea = templates["super_rea"]
        template_rank_up = templates["rank_up"]
        template_no_tap1 = templates["no_tap1"]
        template_no_tap2 = templates["no_tap2"]
        template_no_tap3 = templates["no_tap3"]
        template_clearpicture_buttn = templates["clearpicture_buttn"]
        
        # 画面停滞検出用の変数
        last_screen = None
        last_change_time = time.time()
        
        # 処理のどの段階にいるかを示すステート変数
        current_state = "quest_start"
        
        while True:
            screen = capture_screen(device_id)
            if screen is None:
                time.sleep(0.5)
                continue
            
            # 画面の変化を検出
            if is_screen_changed(last_screen, screen):
                last_screen = screen.copy()
                last_change_time = time.time()
            
            # 画面が停滞しているかチェック
            template_matches, last_change_time = check_screen_stagnation(
                device_id, last_screen, last_change_time, templates
            )
            
            # 画面が停滞していて、マッチしたテンプレートがある場合
            if template_matches:
                # ステートを決定
                if "quest1" in template_matches:
                    current_state = "quest_start"
                    colored_print(device_id, "クエスト開始画面を検出。クエスト開始処理へ移行します。")
                elif "sukeet" in template_matches or "sukeet2" in template_matches:
                    current_state = "sukeet_select"
                    colored_print(device_id, "助っ人選択画面を検出。助っ人選択処理へ移行します。")
                elif "go" in template_matches:
                    current_state = "go_quest"
                    colored_print(device_id, "出撃画面を検出。出撃処理へ移行します。")
                elif "Boss_Hp0" in template_matches:
                    current_state = "boss_defeat"
                    colored_print(device_id, "ボス撃破画面を検出。スワイプ処理へ移行します。")
                elif "clear_ok" in template_matches:
                    current_state = "clear_ok"
                    colored_print(device_id, "クリアOK画面を検出。クリアOK処理へ移行します。")
                elif "special_housyu" in template_matches:
                    current_state = "special_housyu"
                    colored_print(device_id, "特別報酬画面を検出。特別報酬処理へ移行します。")
                elif "result_ok" in template_matches:
                    current_state = "result_ok"
                    colored_print(device_id, "結果OK画面を検出。結果OK処理へ移行します。")
                elif "super_rea" in template_matches:
                    current_state = "super_rea"
                    colored_print(device_id, "スーパーレア画面を検出。スーパーレア処理へ移行します。")
                elif "rank_up" in template_matches:
                    current_state = "rank_up"
                    colored_print(device_id, "ランクアップ画面を検出。ランクアップ処理へ移行します。")
                elif "clearpicture_buttn" in template_matches:
                    current_state = "clear_picture"
                    colored_print(device_id, "クリア写真ボタンを検出。クリア写真処理へ移行します。")
                else:
                    colored_print(device_id, "対応する処理が見つかりませんでした。画面を更新します。")
                    continue
            
            # 現在のステートに応じた処理を実行
            if current_state == "quest_start":
                found, pos = match_template(screen, template_quest1, threshold=0.4)
                if found:
                    center_x = pos[0] + template_quest1.shape[1] // 2
                    center_y = pos[1] + template_quest1.shape[0] // 2
                    colored_print(device_id, "クエスト開始ボタンを検出。タップします。")
                    tap(center_x, center_y, device_id)
                    time.sleep(0.5)
                    current_state = "sukeet_select"
                    continue
            
            elif current_state == "sukeet_select":
                found1, pos1 = match_template(screen, template_sukeet, threshold=0.8)
                found2, pos2 = match_template(screen, template_sukeet2, threshold=0.8)
                found_no1, pos_no1 = match_template(screen, template_no_tap1, threshold=0.8)
                found_no2, pos_no2 = match_template(screen, template_no_tap2, threshold=0.8)
                found_no3, pos_no3 = match_template(screen, template_no_tap3, threshold=0.8)
                
                if found1 or found2:
                    if found1:
                        center_x = pos1[0] + template_sukeet.shape[1] // 2
                        center_y = pos1[1] + template_sukeet.shape[0] // 2
                    else:
                        center_x = pos2[0] + template_sukeet2.shape[1] // 2
                        center_y = pos2[1] + template_sukeet2.shape[0] // 2
                    
                    should_tap = True
                    if found_no1 or found_no2 or found_no3:
                        no_tap_positions = []
                        if found_no1:
                            no_tap_positions.append((pos_no1, template_no_tap1.shape))
                        if found_no2:
                            no_tap_positions.append((pos_no2, template_no_tap2.shape))
                        if found_no3:
                            no_tap_positions.append((pos_no3, template_no_tap3.shape))
                        
                        for no_tap_pos, no_tap_shape in no_tap_positions:
                            if (no_tap_pos[0] <= center_x <= no_tap_pos[0] + no_tap_shape[1] and
                                no_tap_pos[1] <= center_y <= no_tap_pos[1] + no_tap_shape[0]):
                                should_tap = False
                                break
                    
                    if should_tap:
                        colored_print(device_id, "助っ人を検出。タップします。")
                        tap(center_x, center_y, device_id)
                        time.sleep(0.5)
                        current_state = "go_quest"
                        continue
                    else:
                        colored_print(device_id, "助っ人を検出しましたが、タップしない領域と重なっているためスキップします。")
            
            elif current_state == "go_quest":
                found, pos = match_template(screen, template_go, threshold=0.8)
                if found:
                    center_x = pos[0] + template_go.shape[1] // 2
                    center_y = pos[1] + template_go.shape[0] // 2
                    colored_print(device_id, "出撃を検出。タップします。")
                    tap(center_x, center_y, device_id)
                    time.sleep(0.5)
                    current_state = "boss_defeat"
                    continue
            
            elif current_state == "boss_defeat":
                found_clearpicture, pos_clearpicture = match_template(screen, template_clearpicture_buttn, threshold=0.8)
                if found_clearpicture:
                    center_x = pos_clearpicture[0] + template_clearpicture_buttn.shape[1] // 2
                    center_y = pos_clearpicture[1] + template_clearpicture_buttn.shape[0] // 2
                    colored_print(device_id, "クリア写真ボタンを検出。タップします。")
                    tap(center_x, center_y, device_id)
                    time.sleep(0.5)
                    continue
                
                found_test, pos_test = match_template(screen, template_Boss_Hp0, threshold=0.92)
                if found_test:
                    colored_print(device_id, "template\\_boss_hp0を検出しました。スワイプ処理を実施します。")
                    for i in range(45):
                        # 各スワイプ前にtemplate_clear_okをチェック
                        screen = capture_screen(device_id)
                        if screen is not None:
                            found_clear, pos_clear = match_template(screen, template_clear_ok, threshold=0.8)
                            if found_clear:
                                colored_print(device_id, "template_clear_okを検出しました。スワイプ処理を中断します。")
                                current_state = "clear_ok"
                                break

                        start_x = random.randint(100, 1000)
                        start_y = random.randint(100, 1400)
                        end_x = random.randint(100, 1000)
                        end_y = random.randint(100, 1400)

                        if i % 2 == 0:
                            colored_print(device_id, f"左スワイプ {i + 1}/50")
                            swipe(end_x, start_y, start_x, end_y, duration=20, device_id=device_id)
                        else:
                            colored_print(device_id, f"右スワイプ {i + 1}/50")
                            swipe(start_x, start_y, end_x, end_y, duration=20, device_id=device_id)
                    continue
                
                found_menu, pos_menu = match_template(screen, template_menu, threshold=0.8)
                found_clear_ok, pos_clear_ok = match_template(screen, template_clear_ok, threshold=0.8)
                
                if found_clear_ok:
                    colored_print(device_id, "template\\_clear\\_ok を検出。menu処理をスキップします。")
                    current_state = "clear_ok"
                    continue
                elif found_menu:
                    start_x = random.randint(100, 1000)
                    start_y = random.randint(100, 1400)
                    end_x = random.randint(100, 1000)
                    end_y = random.randint(100, 1400)
                    colored_print(device_id, f"template\\_menu を検出。ランダム座標 ({start_x}, {start_y}) から ({end_x}, {end_y}) へスワイプします。")
                    swipe(start_x, start_y, end_x, end_y, duration=100, device_id=device_id)
                else:
                    time.sleep(0.5)
            
            elif current_state == "clear_ok":
                found, pos = match_template(screen, template_clear_ok, threshold=0.95)
                if found:
                    colored_print(device_id, "template\\_clear\\_ok を検出。0.5秒後にスワイプします。")
                    time.sleep(0.5)
                    swipe(541, 1227, 541, 1226, duration=300, device_id=device_id)
                    time.sleep(0.5)
                    current_state = "special_housyu"
                    continue
            
            elif current_state == "special_housyu":
                found, pos = match_template(screen, template_special_housyu, threshold=0.8)
                if found:
                    center_x = pos[0] + template_special_housyu.shape[1] // 2
                    center_y = pos[1] + template_special_housyu.shape[0] // 2
                    colored_print(device_id, "template\\_special\\_housyu を検出。5秒後にスワイプします。")
                    time.sleep(7)
                    swipe(544, 103, 544, 102, duration=300, device_id=device_id)
                    swipe(544, 103, 544, 102, duration=300, device_id=device_id)
                    swipe(544, 103, 544, 102, duration=300, device_id=device_id)
                    time.sleep(2)
                    current_state = "result_ok"
                    continue
            
            elif current_state == "result_ok":
                found, pos = match_template(screen, template_result_ok, threshold=0.8)
                if found:
                    center_x = pos[0] + template_result_ok.shape[1] // 2
                    center_y = pos[1] + template_result_ok.shape[0] // 2
                    colored_print(device_id, "template\\_result\\_ok を検出。1秒後にタップします。")
                    time.sleep(1)
                    tap(center_x, center_y, device_id)
                    time.sleep(0.5)
                    current_state = "super_rea"
                    continue
            
            elif current_state == "super_rea":
                found, pos = match_template(screen, template_super_rea, threshold=0.8)
                if found:
                    center_x = pos[0] + template_super_rea.shape[1] // 2
                    center_y = pos[1] + template_super_rea.shape[0] // 2
                    colored_print(device_id, "template\\_super\\_rea を検出。3秒後にy座標を1減らしたスワイプ処理を行います。")
                    time.sleep(1)
                    swipe(center_x, center_y, center_x, center_y - 1, duration=300, device_id=device_id)
                    time.sleep(0.5)
                    current_state = "rank_up"
                    continue
                else:
                    # 5秒以上経過したらタイムアウト
                    if time.time() - last_change_time >= 5:
                        colored_print(device_id, "template\\_super\\_rea の検出タイムアウト。次のステップへ進みます。")
                        current_state = "rank_up"
                        continue
            
            elif current_state == "rank_up":
                found, pos = match_template(screen, template_rank_up, threshold=0.6)
                if found:
                    center_x = pos[0] + template_rank_up.shape[1] // 2
                    center_y = pos[1] + template_rank_up.shape[0] // 2
                    colored_print(device_id, "template\\_rank\\_up を検出。3秒後にy座標を1減らしたスワイプ処理を行います。")
                    time.sleep(1)
                    swipe(center_x, center_y, center_x, center_y - 1, duration=300, device_id=device_id)
                    time.sleep(0.5)
                    swipe(center_x, center_y, center_x, center_y - 1, duration=300, device_id=device_id)
                    current_state = "quest_start"  # サイクル完了、最初に戻る
                    colored_print(device_id, "1サイクル処理が完了しました。クエスト開始画面へ戻ります。")
                    continue
                else:
                    # 5秒以上経過したらタイムアウト
                    if time.time() - last_change_time >= 5:
                        colored_print(device_id, "template\\_rank\\_up の検出タイムアウト。クエスト開始画面へ戻ります。")
                        current_state = "quest_start"
                        continue
            
            elif current_state == "clear_picture":
                found, pos = match_template(screen, template_clearpicture_buttn, threshold=0.8)
                if found:
                    center_x = pos[0] + template_clearpicture_buttn.shape[1] // 2
                    center_y = pos[1] + template_clearpicture_buttn.shape[0] // 2
                    colored_print(device_id, "クリア写真ボタンを検出。タップします。")
                    tap(center_x, center_y, device_id)
                    time.sleep(0.5)
                    # クリア写真をタップした後は、ボス撃破状態に戻る
                    current_state = "boss_defeat"
                    continue
            
            # 短い待機時間
            time.sleep(0.5)
            
    except Exception as e:
        colored_print(device_id, f"処理中にエラーが発生しました: {e}")

def main():
    parser = argparse.ArgumentParser(description="複数エミュレーターへのクエスト自動周回スクリプト")
    parser.add_argument("--instance", type=int, default=None, help="エミュレーターのインスタンス番号（0～）")
    args = parser.parse_args()

    connected_ips = auto_connect_ports()
    normal_print("接続済みIP: " + str(connected_ips))

    adb_auto_connect()
    adb_devices = get_adb_devices()

    if args.instance is not None:
        if args.instance < len(adb_devices):
            adb_devices = [adb_devices[args.instance]]
            normal_print(f"インスタンス番号 {args.instance} の対象で実行します。")
        else:
            normal_print("指定したインスタンス番号が見つかりません。")
            sys.exit(1)

    # すべてのデバイス用にテンプレート画像を一度だけ読み込む
    templates = {
        "quest1": cv2.imread(r"img\shortcut.png"),
        "sukeet": cv2.imread(r"img\sukeeto.png"),
        "sukeet2": cv2.imread(r"img\sukeeto2.png"),
        "go": cv2.imread(r"img\go.png"),
        "menu": cv2.imread(r"img\menu.png"),
        "Boss_Hp0": cv2.imread(r"img\boss_hp0.png"),
        "clear_ok": cv2.imread(r"img\clear_ok.png"),
        "result_ok": cv2.imread(r"img\result_ok.png"),
        "special_housyu": cv2.imread(r"img\special_housyu.png"),
        "super_rea": cv2.imread(r"img\super_rea.png"),
        "rank_up": cv2.imread(r"img\rank_up.png"),
        "no_tap1": cv2.imread(r"img\no_tap1.png"),
        "no_tap2": cv2.imread(r"img\no_tap2.png"),
        "no_tap3": cv2.imread(r"img\no_tap3.png"),
        "clearpicture_buttn": cv2.imread(r"img\clearpicture_buttn.png")
    }

    # テンプレート画像の読み込みチェック
    for template_name, template in templates.items():
        if template is None:
            normal_print(f"テンプレート画像 {template_name} の読み込みに失敗しました。")
            if template_name == "clearpicture_buttn":
                normal_print("clearpicture_buttn.png が見つかりません。img フォルダに画像を配置してください。")
            return

    normal_print(f"検出されたデバイス数: {len(adb_devices)}")
    normal_print("複数エミュレーターへのクエスト自動周回スクリプトを開始します。")
    
    # 各デバイス用のスレッドを作成し並行実行
    threads = []
    for device_id in adb_devices:
        # デバイスごとのロックを作成
        device_locks[device_id] = threading.Lock()
        
        # 各デバイス用のスレッドを作成
        device_thread = threading.Thread(
            target=lambda d=device_id: device_worker(d, templates),
            name=f"Device-{device_id}"
        )
        device_thread.daemon = True  # メインスレッド終了時に強制終了
        threads.append(device_thread)
        device_thread.start()
        normal_print(f"デバイス {device_id} 用のスレッドを開始しました。")
    
    # すべてのデバイススレッドが終了するまで待機
    try:
        while any(t.is_alive() for t in threads):
            time.sleep(1)
    except KeyboardInterrupt:
        normal_print("スクリプトを終了します...")

def device_worker(device_id, templates):
    """デバイスごとのワーカースレッド"""
    colored_print(device_id, "ワーカースレッドを開始します。")
    try:
        while True:
            # デバイスごとの処理を実行
            with device_locks[device_id]:
                process_device(device_id, templates)
            # 短い待機時間（必要に応じて調整）
            time.sleep(1)
    except Exception as e:
        colored_print(device_id, f"ワーカースレッドでエラーが発生しました: {e}")

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        normal_print("スクリプトを終了しました。")

