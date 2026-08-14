import os
import re
import threading
import time
import winsound
from datetime import datetime
import cv2
import easyocr
import mysql.connector
import numpy as np
from dotenv import load_dotenv
from PIL import Image, ImageDraw, ImageFont
import requests
from ultralytics import YOLO

# ==========================================
# 1. โหลดข้อมูลสภาพแวดล้อม (ENV & Config)
# ==========================================
load_dotenv()

DB_CONFIG = {
    'host': os.getenv('DB_HOST', 'localhost'),
    'user': os.getenv('DB_USER', 'root'),
    'password': os.getenv('DB_PASSWORD', ''),
    'database': os.getenv('DB_NAME', 'company_db'),
}

DISCORD_WEBHOOK_URL = os.getenv('DISCORD_WEBHOOK_URL', '')
SNAPSHOT_DIR = 'snapshots'
os.makedirs(SNAPSHOT_DIR, exist_ok=True)


# ==========================================
# 2. ฟังก์ชันเสริมความแม่นยำและระบบแจ้งเตือน
# ==========================================
def clean_thai_plate(text):
    """กรองเอาเฉพาะ พยัญชนะไทย (ก-ฮ) และตัวเลข (0-9)"""
    cleaned = re.sub(r'[^ก-ฮ0-9]', '', text)
    if 3 <= len(cleaned) <= 8:
        return cleaned
    return None


def preprocess_plate(crop_img):
    """ปรับภาพให้คมชัดด้วย CLAHE ก่อนส่งอ่าน OCR"""
    gray = cv2.cvtColor(crop_img, cv2.COLOR_BGR2GRAY)
    clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
    enhanced = clahe.apply(gray)
    return enhanced


def play_sound_alert(status):
    """เสียงแจ้งเตือนการตรวจจับ"""
    try:
        if status == 'REGISTERED':
            winsound.Beep(1500, 150)  # เสียง ติ๊ด สั้นๆ (รถในระบบ)
        elif status == 'BLACKLIST_ALERT':
            winsound.Beep(800, 200)  # เสียงเตือนภัย 2 ครั้ง (รถต้องสงสัย)
            winsound.Beep(800, 200)
        else:
            winsound.Beep(1000, 200)  # เสียงเตือนรถภายนอก
    except Exception:
        pass


def send_notification(message):
    """ส่งการแจ้งเตือนไปยัง Discord Webhook"""
    if not DISCORD_WEBHOOK_URL or 'YOUR_WEBHOOK_URL' in DISCORD_WEBHOOK_URL:
        return
    try:
        requests.post(
            DISCORD_WEBHOOK_URL, json={'content': message}, timeout=2
        )
    except Exception:
        pass


def draw_thai_text(
    img,
    text,
    position,
    font_path='C:\\Windows\\Fonts\\tahoma.ttf',
    font_size=22,
    color=(0, 255, 0),
):
    """วาดข้อความภาษาไทยลงบนภาพด้วย PIL/Pillow"""
    img_pil = Image.fromarray(cv2.cvtColor(img, cv2.COLOR_BGR2RGB))
    draw = ImageDraw.Draw(img_pil)
    try:
        font = ImageFont.truetype(font_path, font_size)
    except IOError:
        try:
            font = ImageFont.truetype('tahoma.ttf', font_size)
        except IOError:
            font = ImageFont.load_default()

    draw.text(position, text, font=font, fill=color)
    return cv2.cvtColor(np.array(img_pil), cv2.COLOR_RGB2BGR)


def check_and_log_vehicle(plate_text, snapshot_path=None):
    """ตรวจสอบทะเบียนและบันทึกการขับผ่านเข้าวิทยาลัย"""
    conn, cursor = None, None
    try:
        conn = mysql.connector.connect(**DB_CONFIG)
        cursor = conn.cursor(dictionary=True)

        cursor.execute(
            'SELECT * FROM personnel_vehicles WHERE license_plate = %s',
            (plate_text,),
        )
        vehicle = cursor.fetchone()

        status, emp_name = 'VISITOR', None
        if vehicle:
            emp_name = vehicle['name']
            status = (
                'BLACKLIST_ALERT'
                if vehicle['status'] == 'BLACKLIST'
                else 'REGISTERED'
            )

        log_query = 'INSERT INTO access_logs (detected_plate, status, employee_name, snapshot_path) VALUES (%s, %s, %s, %s)'
        cursor.execute(
            log_query, (plate_text, status, emp_name, snapshot_path)
        )
        conn.commit()
        return vehicle, status
    except Exception as e:
        print(f'[Database Error] {e}')
        return None, 'ERROR'
    finally:
        if cursor:
            cursor.close()
        if conn and conn.is_connected():
            conn.close()


# ==========================================
# 3. เริ่มต้นระบบ AI และตัวแปรควบคุม
# ==========================================
print('[System] กำลังโหลดโมเดล SmartPlate สำหรับวิทยาลัย...')
model = YOLO('yolov8n.pt')
reader = easyocr.Reader(['th', 'en'], gpu=False)

cap = cv2.VideoCapture(0)

cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)

# ตัวแปรควบคุม Thread และ Cooldown
ocr_is_busy = False
last_scanned_time = 0
cooldown_time = 5  # เว้นช่วง 5 วินาทีต่อคัน เพื่อไม่ให้นับรถคันเดิมซ้ำ
last_plate = ''

current_boxes = []
current_plate_text = ''

# 📊 ตัวนับสถิติประเภทรถที่ขับผ่าน
registered_count = 0  # รถในระบบ (อาจารย์/นักศึกษา/บุคลากร)
visitor_count = 0  # รถภายนอก / ผู้มาติดต่อ


# ==========================================
# 4. Background OCR Thread Functions
# ==========================================
def process_ocr_async(plate_crop):
    global ocr_is_busy, last_plate, last_scanned_time, current_plate_text
    global registered_count, visitor_count

    ocr_is_busy = True

    enhanced_crop = preprocess_plate(plate_crop)
    ocr_results = reader.readtext(enhanced_crop)

    if ocr_results:
        raw_text = ''.join([res[1] for res in ocr_results])
        clean_text = clean_thai_plate(raw_text)

        if clean_text:
            current_plate_text = clean_text
            current_time = time.time()

            # สแกนผ่านแล้วนับเลย (หากเกิน Cooldown time)
            if clean_text != last_plate or (
                current_time - last_scanned_time
            ) > cooldown_time:

                # เซฟรูป Snapshot ภาพรถที่ขับผ่านไว้เป็นหลักฐาน
                time_str = datetime.now().strftime('%Y%m%d_%H%M%S')
                snapshot_path = f'{SNAPSHOT_DIR}/{clean_text}_{time_str}.jpg'
                cv2.imwrite(snapshot_path, plate_crop)

                # บันทึกข้อมูลเข้า MySQL
                vehicle, status = check_and_log_vehicle(
                    clean_text, snapshot_path
                )

                # สรุปผลและนับจำนวนการเข้า-ออก
                if status == 'REGISTERED':
                    registered_count += 1
                    msg = f"🚗 [รถในระบบ] คุณ {vehicle['name']} ({vehicle['department']}) - ป้าย: {clean_text}"
                elif status == 'BLACKLIST_ALERT':
                    visitor_count += 1
                    msg = f"🚨 [รถต้องสงสัย/BLACKLIST] คุณ {vehicle['name']} - ป้าย: {clean_text}"
                else:
                    visitor_count += 1
                    msg = f'🚙 [รถบุคคลภายนอก] ป้าย: {clean_text} (บันทึกหลักฐานเรียบร้อย)'

                print(msg)
                send_notification(msg)
                play_sound_alert(status)

                last_plate = clean_text
                last_scanned_time = current_time

    ocr_is_busy = False


# ==========================================
# 5. Main Processing Loop
# ==========================================
frame_count = 0
prev_time = time.time()
fps = 0

print('[System] ระบบนับรถอัตโนมัติพร้อมทำงาน (กด "q" เพื่อปิด)')

while cap.isOpened():
    ret, frame = cap.read()
    if not ret:
        break

    frame_count += 1

    curr_time = time.time()
    if curr_time - prev_time >= 1.0:
        fps = frame_count
        frame_count = 0
        prev_time = curr_time

    # ตรวจจับตำแหน่งป้ายทะเบียน
    if frame_count % 3 == 0:
        results = model(frame, verbose=False, conf=0.4)
        current_boxes = []

        for r in results:
            for box in r.boxes:
                x1, y1, x2, y2 = map(int, box.xyxy[0])
                current_boxes.append((x1, y1, x2, y2))

                if not ocr_is_busy:
                    plate_crop = frame[y1:y2, x1:x2]
                    if plate_crop.size > 0:
                        threading.Thread(
                            target=process_ocr_async,
                            args=(plate_crop.copy(),),
                            daemon=True,
                        ).start()

    # --- ส่วนวาด UI บน Dashboard ---
    for x1, y1, x2, y2 in current_boxes:
        cv2.rectangle(frame, (x1, y1), (x2, y2), (0, 255, 0), 2)
        if current_plate_text:
            frame = draw_thai_text(
                frame,
                f'ป้าย: {current_plate_text}',
                (x1, max(y1 - 35, 10)),
                color=(0, 255, 0),
            )

    # 1. แสดง FPS มุมซ้ายบน
    frame = draw_thai_text(
        frame,
        f'FPS: {fps}',
        (10, 15),
        font_size=22,
        color=(0, 255, 255),
    )

    # 2. แสดงสถิติตัวนับรถเข้าวิทยาลัย มุมขวาบน
    stats_text = f'รถในระบบ: {registered_count} | รถภายนอก: {visitor_count}'
    frame = draw_thai_text(
        frame,
        stats_text,
        (320, 15),
        font_size=22,
        color=(255, 255, 255),
    )

    cv2.imshow('SmartPlate Dashboard', frame)

    if cv2.waitKey(1) & 0xFF == ord('q'):
        break

cap.release()
cv2.destroyAllWindows()