"""Buoc 3 cua ke hoach mock telemetry - xem docs/telemetry-research.md.

Modules:
  schemas.py  - xac thuc payload theo 2 schema nhap cua Buoc 2
  store.py    - luu tru + do do tre ingest
  drivers.py  - may trang thai tai xe ao (logic thuan, khong goi mang)
  service.py  - FastAPI app expose POST /telemetry/gps-ping, /telemetry/trip-event
  producer.py - vong lap goi drivers.tick() va POST len service qua HTTP that
"""
