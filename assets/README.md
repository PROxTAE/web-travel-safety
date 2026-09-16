# Smart Travel Assistant — UI & Asset Pack

ชุดภาพนี้ใช้ `ui-screens/01-dashboard-overview.png` เป็น visual master และขยายระบบให้ใช้งานได้ทั่วโลก โดยคงโทนมิ้นต์–เขียว–ส้ม งานแผนที่แบบ illustration และมาสคอตช้างนักเดินทางไว้เหมือนกันทุกหน้า

## UI screens

| File | Purpose |
| --- | --- |
| `ui-screens/01-dashboard-overview.png` | หน้าภาพรวม การเดินทาง ความเสี่ยง คำแนะนำ และ SOS |
| `ui-screens/02-trip-planner.png` | สร้างทริป เลือกต้นทาง–ปลายทาง วันเดินทาง และรูปแบบการเดินทาง |
| `ui-screens/03-global-safety-map.png` | แผนที่ความเสี่ยงทั่วโลกพร้อม layer และรายละเอียดเหตุการณ์ |
| `ui-screens/04-ai-assistant.png` | แชตกับ AI โดยใช้บริบททริป อากาศ และการเดินทาง |
| `ui-screens/05-emergency-center.png` | ศูนย์ฉุกเฉิน แชร์ตำแหน่ง และค้นหาความช่วยเหลือในประเทศปัจจุบัน |
| `ui-screens/06-route-comparison.png` | เปรียบเทียบเส้นทางเดิมกับเส้นทางปลอดภัยก่อนยืนยัน |

## Interaction flow

### Dashboard

| Click / action | Result |
| --- | --- |
| `My Trip` | เปิด `02-trip-planner.png` |
| `Safety Map` | เปิด `03-global-safety-map.png` |
| `Assistant` หรือช่องถาม AI | เปิด `04-ai-assistant.png` พร้อมส่งบริบททริปปัจจุบัน |
| `Emergency` หรือ `SOS` | เปิด `05-emergency-center.png` |
| `Use safer route` / `Safer route found` | เปิด `06-route-comparison.png` |
| Weather / Transport / Risk card | เปิด side sheet รายละเอียดของหมวดนั้น |
| Marker บนแผนที่ | เปิด alert card ของเหตุการณ์ที่เลือก |

### Trip Planner

- แก้ไข `From`, `To`, `Departure`, `Return`, travel mode และ preferences ได้
- `Find safe routes` ตรวจอากาศ การเดินทาง และความเสี่ยง แล้วแสดง Recommended / Fastest / Lowest risk
- เลือก route card แล้วเข้าสู่หน้า `06-route-comparison.png` ก่อนบันทึก

### Global Safety Map

- Toggle ใน `Risk Layers` แสดงหรือซ่อน Weather, Transport, Natural hazards และ Health & safety
- คลิก marker เพื่อเปิดรายละเอียดและเวลาที่อัปเดตล่าสุด
- `View details` เปิดรายละเอียดเต็มของเหตุการณ์
- `Avoid area` คำนวณทางเลือกแล้วเปิด `06-route-comparison.png`
- Timeline `Now / 6 hr / 12 hr` เปลี่ยนข้อมูลคาดการณ์บนแผนที่

### AI Assistant

- Quick chip ส่งคำถามสำเร็จรูปเข้าแชตทันที
- `Use live location` ขอ permission ก่อนส่งตำแหน่งให้ AI
- `Update my trip` บันทึกข้อเสนอของ AI และเปิดหน้าตรวจสอบเส้นทาง
- `Emergency help` เปิด `05-emergency-center.png`

### Emergency Center

- `Hold for SOS` ต้องกดค้าง 3 วินาที จากนั้นเปิดขั้น Confirm ก่อนแชร์ตำแหน่งหรือเชื่อมต่อความช่วยเหลือ
- `Share location` ขอ location permission และแสดงสถานะแชร์แบบ real time
- `Find nearest` ใช้ประเทศ/ตำแหน่งปัจจุบันเพื่อค้นหาหน่วยฉุกเฉิน โรงพยาบาล หรือสถานทูต
- `Review details` เปิดข้อมูลสุขภาพ ผู้ติดต่อฉุกเฉิน และประกันเดินทางให้ตรวจสอบก่อนส่ง

### Route Comparison

- `Apply safer route` อัปเดต itinerary, alerts และ transport details แล้วแสดง toast `Trip updated`
- `Keep original` ต้องติ๊กยอมรับความเสี่ยงก่อนดำเนินการ
- `Notify me about changes` เปิดการแจ้งเตือนเมื่อความเสี่ยงหรือเส้นทางเปลี่ยน

## Branding and reusable graphics

- `branding/logo-horizontal.png` — โลโก้แนวนอน พื้นหลังโปร่งใส
- `branding/app-logo-mark.png` — app/favicon mark พื้นหลังโปร่งใส
- `mascot/mascot-welcome.png` — ท่าต้อนรับ
- `mascot/mascot-warning.png` — ท่าแจ้งเตือน
- `mascot/mascot-emergency-help.png` — ท่าช่วยเหลือฉุกเฉิน
- `illustrations/hero-global-travel-banner.png` — hero/banner กว้าง 3:1
- `illustrations/background-topographic-pattern.png` — ลายพื้นหลังสำหรับ section หรือ page shell
- `illustrations/global-map-background.png` — world map เปล่าสำหรับวาง route และ marker ทับ
- `icons/travel-safety-icon-sheet.png` — sprite sheet ขนาด 4 × 3
- `icons/*.png` — ไอคอนเดี่ยว 12 ไฟล์ ขนาด 362 × 362 พื้นหลังโปร่งใส

## Suggested design tokens

| Token | Value |
| --- | --- |
| Primary teal | `#08B88A` |
| Deep teal | `#087B73` |
| Mint surface | `#DDF9EE` |
| Background | `#F5FFFC` |
| Heading navy | `#101A4B` |
| Weather blue | `#2F86F6` |
| Warning amber | `#FF9D1F` |
| Emergency coral | `#F24E54` |

## Implementation notes

- โลโก้ มาสคอต และไอคอนเป็น PNG แบบ alpha transparency พร้อมวางบนพื้นหลังเว็บ
- ควรใช้ไฟล์แผนที่เป็น visual base เท่านั้น ส่วน marker, route, weather และ risk layer ควรสร้างเป็น UI overlay เพื่ออัปเดตแบบ real time
- สำหรับ responsive layout ให้ซ่อน illustration ด้านข้างก่อน แล้วค่อยยุบ panel เป็น drawer บนหน้าจอขนาดเล็ก

