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

ADB_PATH = r"C:\adb\adb.exe"
WEBHOOK_URL = "https://discord.com/api/webhooks/1351841313439612938/6VK9MD3hAGe0Yzuhp03Lu8mLQGR7b6YjoVUz3h8LIP9OvIhTPBkpFY8hR14BfVjv1P7l"

logging.getLogger().setLevel(logging.WARNING)


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

def main():
    parser = argparse.ArgumentParser(description="複数エミュレーターへのクエスト自動周回スクリプト")
    parser.add_argument("--instance", type=int, default=None, help="エミュレーターのインスタンス番号（0～）")
    args = parser.parse_args()

    connected_ips = auto_connect_ports()
    print("接続済みIP:", connected_ips)

    adb_auto_connect()
    adb_devices = get_adb_devices()

    if args.instance is not None:
        if args.instance < len(adb_devices):
            adb_devices = [adb_devices[args.instance]]
            print(f"インスタンス番号 {args.instance} の対象で実行します。")
        else:
            print("指定したインスタンス番号が見つかりません。")
            sys.exit(1)

    template_quest1 = cv2.imread(r"img\quest.png")
    template_quest2 = cv2.imread(r"img\quest2.png")
    template_normal = cv2.imread(r"img\normal.png")
    template_solo = cv2.imread(r"img\solo.png")
    template_my_chara = cv2.imread(r"img\my_chara.png")
    template_sukeet = cv2.imread(r"img\sukeeto.png")
    template_sukeet2 = cv2.imread(r"img\sukeeto2.png")
    template_go = cv2.imread(r"img\go.png")
    template_menu = cv2.imread(r"img\menu.png")
    template_Boss_Hp0 = cv2.imread(r"img\boss_hp0.png")
    template_clear_ok = cv2.imread(r"img\clear_ok.png")
    template_result_ok = cv2.imread(r"img\result_ok.png")
    template_special_housyu = cv2.imread(r"img\special_housyu.png")
    template_super_rea = cv2.imread(r"img\super_rea.png")
    template_rank_up = cv2.imread(r"img\rank_up.png")
    template_no_tap1 = cv2.imread(r"img\no_tap1.png")
    template_no_tap2 = cv2.imread(r"img\no_tap2.png")
    template_no_tap3 = cv2.imread(r"img\no_tap3.png")

    if template_quest1 is None or template_rank_up is None:
        print("テンプレート画像の読み込みに失敗しました。画像パスやファイルを確認してください。")
        return

    print("複数エミュレーターへのクエスト自動周回スクリプトを開始します。")
    while True:
        for device_id in adb_devices:
            screen = capture_screen(device_id)
            if screen is None:
                continue

            # クエスト開始ボタン検出とタップ
            while True:
                found1, pos1 = match_template(screen, template_quest1, threshold=0.4)
                found2, pos2 = match_template(screen, template_quest2, threshold=0.4)
                if found1 or found2:
                    if found1:
                        center_x = pos1[0] + template_quest1.shape[1] // 2
                        center_y = pos1[1] + template_quest1.shape[0] // 2
                    else:
                        center_x = pos2[0] + template_quest2.shape[1] // 2
                        center_y = pos2[1] + template_quest2.shape[0] // 2
                    print(f"デバイス {device_id} でクエスト開始ボタンを検出。タップします。")
                    tap(center_x, center_y, device_id)
                    time.sleep(0.5)
                    break
                time.sleep(0.5)
                screen = capture_screen(device_id)

            # ノーマルボタン検出とタップ、その後通知
            while True:
                found, pos = match_template(screen, template_normal, threshold=0.8)
                if found:
                    center_x = pos[0] + template_normal.shape[1] // 2
                    center_y = pos[1] + template_normal.shape[0] // 2
                    print(f"デバイス {device_id} でノーマルボタンを検出。タップします。")
                    tap(center_x, center_y, device_id)
                    print(f"デバイス {device_id} でクエストを選択してください。")
                    send_discord_webhook("クエストを選択してください。")
                    time.sleep(0.5)
                    break
                time.sleep(0.5)
                screen = capture_screen(device_id)

            # ソロボタン検出とタップ
            while True:
                found, pos = match_template(screen, template_solo, threshold=0.8)
                if found:
                    center_x = pos[0] + template_solo.shape[1] // 2
                    center_y = pos[1] + template_solo.shape[0] // 2
                    print(f"デバイス {device_id} でソロボタンを検出。タップします。")
                    tap(center_x, center_y, device_id)
                    time.sleep(0.5)
                    break
                time.sleep(0.5)
                screen = capture_screen(device_id)
            """

            # キャラクター画像検出後、固定座標でスワイプ
            while True:
                found, pos = match_template(screen, template_my_chara, threshold=0.8)
                if found:
                    start_x, start_y = 510, 1650
                    end_x, end_y = 510, 70
                    print(f"デバイス {device_id} で固定座標 ({start_x}, {start_y}) から ({end_x}, {end_y}) へスワイプします。")
                    swipe(start_x, start_y, end_x, end_y, duration=200, device_id=device_id)
                    time.sleep(3)
                    break
                time.sleep(0.5)
                screen = capture_screen(device_id)
            """
            # 助っ人検出とタップ（Tapしないエリアも考慮）
            while True:
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
                        print(f"デバイス {device_id} で助っ人を検出。タップします。")
                        tap(center_x, center_y, device_id)
                        time.sleep(0.5)
                        break
                    else:
                        print(f"デバイス {device_id} で助っ人を検出しましたが、タップしない領域と重なっているためスキップします。")
                time.sleep(0.5)
                screen = capture_screen(device_id)

            # 出撃検出とタップ
            while True:
                found, pos = match_template(screen, template_go, threshold=0.8)
                if found:
                    center_x = pos[0] + template_go.shape[1] // 2
                    center_y = pos[1] + template_go.shape[0] // 2
                    print(f"デバイス {device_id} で出撃を検出。タップします。")
                    tap(center_x, center_y, device_id)
                    time.sleep(5)
                    break
                time.sleep(0.5)
                screen = capture_screen(device_id)

            # template_StageClear 検出待ち・template_menu 検出時のランダムスワイプ処理
            while True:
                screen = capture_screen(device_id)
                if screen is None:
                    time.sleep(0)  # 0秒待機
                    continue
                found_test, pos_test = match_template(screen, template_Boss_Hp0, threshold=0.92)
                # テンプレート_TapGet 検出処理（修正後）
                if found_test:
                    print(f"デバイス {device_id} にて template\\_boss_hp0を検出しました。スワイプ処理を実施します。")
                    screen = capture_screen(device_id)
                    if screen is None:
                        print(f"デバイス {device_id} で画面取得に失敗しました。左右スワイプ処理をスキップします。")
                    else:
                        for i in range(50):
                            start_x = random.randint(100, 1000)
                            start_y = random.randint(100, 1400)
                            end_x = random.randint(100, 1000)
                            end_y = random.randint(100, 1400)

                            if i % 2 == 0:
                                print(f"デバイス {device_id} で左スワイプ {i + 1}/50")
                                swipe(end_x, start_y, start_x, end_y, duration=20, device_id=device_id)
                            else:
                                print(f"デバイス {device_id} で右スワイプ {i + 1}/50")
                                swipe(start_x, start_y, end_x, end_y, duration=20, device_id=device_id)
                    break

                # template_menu 検出時のランダムスワイプ処理
                found_menu, pos_menu = match_template(screen, template_menu, threshold=0.8)
                if found_menu:
                    start_x = random.randint(100, 1000)
                    start_y = random.randint(100, 1400)
                    end_x = random.randint(100, 1000)
                    end_y = random.randint(100, 1400)
                    print(
                        f"デバイス {device_id} で template\\_menu を検出。ランダム座標 ({start_x}, {start_y}) から ({end_x}, {end_y}) へスワイプします。")
                    swipe(start_x, start_y, end_x, end_y, duration=100, device_id=device_id)
                else:
                    print(f"デバイス {device_id} で template\\_menu は検出されませんでした。")

            # template_clear_ok 検出後、短いスワイプ処理
            while True:
                screen = capture_screen(device_id)
                if screen is None:
                    time.sleep(0.5)
                    continue
                found, pos = match_template(screen, template_clear_ok, threshold=0.8)
                if found:
                    print(f"デバイス {device_id} で template\\_clear\\_ok を検出。1秒後にスワイプします。")
                    time.sleep(1)
                    swipe(541, 1227, 541, 1226, duration=300, device_id=device_id)
                    time.sleep(0.5)
                    break
                time.sleep(0.5)

            # template_special_housyu 検出後、3秒後にスワイプ処理
            while True:
                screen = capture_screen(device_id)
                if screen is None:
                    time.sleep(0.5)
                    continue
                found, pos = match_template(screen, template_special_housyu, threshold=0.8)
                if found:
                    center_x = pos[0] + template_special_housyu.shape[1] // 2
                    center_y = pos[1] + template_special_housyu.shape[0] // 2
                    print(f"デバイス {device_id} で template\\_special\\_housyu を検出。3秒後にスワイプします。")
                    time.sleep(3)
                    swipe(544, 103, 544, 102, duration=300, device_id=device_id)
                    time.sleep(0.5)
                    break
                time.sleep(0.5)

            # template_result_ok 検出後、1秒後にタップ
            while True:
                screen = capture_screen(device_id)
                if screen is None:
                    time.sleep(0.5)
                    continue
                found, pos = match_template(screen, template_result_ok, threshold=0.8)
                if found:
                    center_x = pos[0] + template_result_ok.shape[1] // 2
                    center_y = pos[1] + template_result_ok.shape[0] // 2
                    print(f"デバイス {device_id} で template\\_result\\_ok を検出。1秒後にタップします。")
                    time.sleep(1)
                    tap(center_x, center_y, device_id)
                    time.sleep(0.5)
                    break
                time.sleep(0.5)

            # template_super_rea 検出後、y座標を1減らしたスワイプ処理（5秒以内）
            start_time = time.time()
            while True:
                screen = capture_screen(device_id)
                if screen is None:
                    time.sleep(0.5)
                    if time.time() - start_time >= 5:
                        print(f"デバイス {device_id} で template\\_super\\_rea の検出タイムアウト。")
                        break
                    continue
                found, pos = match_template(screen, template_super_rea, threshold=0.8)
                if found:
                    center_x = pos[0] + template_super_rea.shape[1] // 2
                    center_y = pos[1] + template_super_rea.shape[0] // 2
                    print(f"デバイス {device_id} で template\\_super\\_rea を検出。3秒後にy座標を1減らしたスワイプ処理を行います。")
                    time.sleep(1)
                    swipe(center_x, center_y, center_x, center_y - 1, duration=300, device_id=device_id)
                    time.sleep(0.5)
                    break
                if time.time() - start_time >= 5:
                    print(f"デバイス {device_id} で template\\_super\\_rea の検出タイムアウト。")
                    break
                time.sleep(0.5)

            # template_rank_up 検出後、y座標を1減らしたスワイプ処理（5秒以内）
            start_time = time.time()
            while True:
                screen = capture_screen(device_id)
                if screen is None:
                    time.sleep(0.5)
                    if time.time() - start_time >= 5:
                        print(f"デバイス {device_id} で template\\_rank\\_up の検出タイムアウト。")
                        break
                    continue
                found, pos = match_template(screen, template_rank_up, threshold=0.6)
                if found:
                    center_x = pos[0] + template_rank_up.shape[1] // 2
                    center_y = pos[1] + template_rank_up.shape[0] // 2
                    print(f"デバイス {device_id} で template\\_rank\\_up を検出。3秒後にy座標を1減らしたスワイプ処理を行います。")
                    time.sleep(1)
                    swipe(center_x, center_y, center_x, center_y - 1, duration=300, device_id=device_id)
                    time.sleep(0.5)
                    swipe(center_x, center_y, center_x, center_y - 1, duration=300, device_id=device_id)
                    break
                if time.time() - start_time >= 5:
                    print(f"デバイス {device_id} で template\\_rank\\_up の検出タイムアウト。")
                    break
                time.sleep(0.5)
        time.sleep(0.5)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("スクリプトを終了しました。")