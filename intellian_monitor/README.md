# Intellian OneWeb Monitor (for UP² / UP Squared)

โปรแกรม Python สำหรับดึงและบันทึกข้อมูลจาก **Intellian OneWeb User Terminal / CNX** ลงในเครื่อง (รองรับทั้ง Linux บนบอร์ด UP² และ Windows)

## ข้อมูลที่ดึงและบันทึก (4 ส่วนหลัก)
1. **พิกัดดาวเทียม & พิกัด User Terminal** (`ut_lat`, `ut_lon`, `ut_alt_m`, `sat_id`, `sat_name`, `sat_azimuth_deg`, `sat_elevation_deg`) จาก Intellian Local User Interface (LUI)
2. **TLE (Two-Line Element)** ดึงข้อมูล OneWeb TLE/OMM JSON จาก CelesTrak (`GROUP=oneweb`) แคชลงไฟล์ `data/tle/oneweb_latest.tle` และจับคู่กับดาวเทียมที่ User Terminal กำลังเชื่อมต่อ
3. **ความแรงสัญญาณ** (`snr_db`, `cn0_dbhz`, `rssi_dbm`, `link_state`)
4. **Ping แบบ Realtime** ตรวจสอบ Latency (`rtt_ms`) และ Packet Loss (`%`) ของ `192.168.100.1` (Intellian CNX) และ `8.8.8.8` (Internet ผ่านดาวเทียม)

## โครงสร้างไฟล์
- `main.py` — ตัวรันหลัก (รองรับโหมดปกติ, `--mock`, `--discover`, `--once`)
- `intellian_client.py` — ดึงข้อมูลจาก Intellian CNX (`http://192.168.100.1`) และตัวช่วยสแกนหา Endpoint
- `tle_fetcher.py` — ดึงและแคช OneWeb TLE จาก CelesTrak
- `ping_monitor.py` — Ping แบบ Realtime ข้ามแพลตฟอร์ม (Linux / Windows)
- `storage.py` — บันทึกลง SQLite (`data/intellian_telemetry.db`), CSV (`data/csv/`), และ JSONL (`data/jsonl/`)
- `config.json` — ตั้งค่า IP, username/password (ถ้ามี), ช่วงเวลาดึงข้อมูล, และเป้าหมาย Ping

## การติดตั้งบน UP² (Ubuntu/Linux)
```bash
pip3 install -r requirements.txt
```

## การต่อสาย LAN และการตั้งค่า IP
1. ต่อสาย LAN จากพอร์ต `MGMT` หรือ `LAN1` ของ **Intellian CNX** เข้ากับพอร์ต LAN ของ **UP²**
2. ตรวจสอบว่า UP² ได้รับ IP ในวง `192.168.100.x` (DHCP หรือตั้ง Static เช่น `192.168.100.50` / Subnet `255.255.255.128`)

## วิธีใช้งาน
### 1. สแกนหา API Endpoint ของ Intellian CNX (ทำครั้งแรกเมื่อต่อกับเครื่องจริง)
```bash
python3 main.py --discover
```
ผลลัพธ์จะถูกบันทึกไว้ที่ `data/discovery_report.json` เพื่อดูว่าเฟิร์มแวร์รุ่นที่ใช้อยู่เปิดข้อมูลที่ path ใดบ้าง (และสามารถนำมาเพิ่มใน `config.json` -> `intellian.endpoints` ได้)

### 2. รันเก็บข้อมูลแบบ Realtime
```bash
python3 main.py
```
กด `Ctrl+C` เพื่อหยุดการทำงาน

### 3. ทดสอบระบบโดยไม่ต้องต่อเครื่องจริง (Mock Mode)
```bash
python3 main.py --mock --once
```
