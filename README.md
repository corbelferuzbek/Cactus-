# Aql Agent — Render Hosting uchun tayyor

Bu paket **Render.com** (yoki shunga o'xshash PaaS) da ishga tushirish uchun tayyorlangan.

## Render'da joylashtirish

1. GitHub/GitLab ga yuklang (yoki Render Dashboard → New → Web Service → "Deploy from existing code" / ZIP).
2. **Root Directory**: bo'sh qoldiring (bu zip ildizda `app.py` bor).
3. **Build Command**: `pip install -r requirements.txt`
4. **Start Command**: `gunicorn app:app --bind 0.0.0.0:$PORT --workers 1 --threads 4 --timeout 120`
   (yoki Procfile avtomatik o'qiladi)
5. Environment:
   - `PYTHON_VERSION` = `3.12.6` (ixtiyoriy)
6. Deploy qiling.

Brauzerda ochilgandan keyin **⚙ Sozlamalar** orqali API kalit va modelni kiriting.

## Muhim ogohlantirish

- `/api/run`, `/api/terminal`, `/api/install` endpointlari **haqiqiy kod va shell buyruqlarini** bajaradi.
- Public internetga ochiq serverda ishlatish xavfli — begonalar serveringizda istalgan buyruqni ishga tushirishi mumkin.
- Faqat shaxsiy foydalanish yoki kuchli autentifikatsiya qo'shilgandan keyin oching.
- Render'da disk ephemeral: `data/` va `sandbox/` redeploy/restart da o'chishi mumkin. Sozlamalarni qayta kiriting.

## Mahalliy ishga tushirish

```bash
pip install -r requirements.txt
python app.py
# yoki
gunicorn app:app --bind 0.0.0.0:5000
```

Port `$PORT` muhit o'zgaruvchisidan olinadi (Render avtomatik beradi).
