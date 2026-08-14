CREATE DATABASE IF NOT EXISTS company_db CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
USE company_db;

-- 1. ตารางข้อมูลรถยนต์ของบุคลากร
CREATE TABLE IF NOT EXISTS personnel_vehicles (
    id INT AUTO_INCREMENT PRIMARY KEY,
    employee_id VARCHAR(50) NOT NULL,
    name VARCHAR(100) NOT NULL,
    license_plate VARCHAR(20) NOT NULL UNIQUE,
    department VARCHAR(50),
    status VARCHAR(20) DEFAULT 'NORMAL' -- NORMAL หรือ BLACKLIST
);

-- 2. ตารางบันทึกประวัติการเข้า-ออก (Access Logs)
CREATE TABLE IF NOT EXISTS access_logs (
    log_id INT AUTO_INCREMENT PRIMARY KEY,
    timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    detected_plate VARCHAR(20),
    status VARCHAR(20), -- ALLOWED, DENIED, BLACKLIST_ALERT
    employee_name VARCHAR(100) DEFAULT NULL,
    snapshot_path VARCHAR(255) DEFAULT NULL
);

-- เพิ่มข้อมูลจำลองสำหรับทดสอบระบบ
INSERT INTO personnel_vehicles (employee_id, name, license_plate, department, status)
VALUES 
    ('EM001', 'สมชาย ใจดี', '1กข1234', 'IT', 'NORMAL'),
    ('EM002', 'สมศรี มีตังค์', '2กข5678', 'HR', 'BLACKLIST')
ON DUPLICATE KEY UPDATE name=VALUES(name);