from fastapi import FastAPI, Request, Form, UploadFile, File, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
import httpx
import os
import mimetypes
from cachetools import TTLCache

app = FastAPI()

# --- الإعدادات ---
if os.path.exists("static"):
    app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")

# بيانات Supabase
SUPABASE_URL = "https://fdrdnejujpuffdazxdmd.supabase.co" 
SUPABASE_KEY = "sb_publishable_CpkFdoxIUmNmvj8pd_Wldw_oOUuQkXs"
HEADERS = {
    "apikey": SUPABASE_KEY,
    "Authorization": f"Bearer {SUPABASE_KEY}",
    "Content-Type": "application/json",
    "Prefer": "return=minimal"
}

# بيانات Telegram (التي استخرجتها أنت)
TELEGRAM_TOKEN = "8506181612:AAHNlB_C0Z4UOq74rJ86LuY2JElyZ9Aop5c"
CHAT_ID = "7476774592"

# كاش للمنتجات لتسريع الموقع
products_cache = TTLCache(maxsize=100, ttl=600)

# --- الدوال المساعدة ---

async def send_telegram_notification(order):
    """إرسال تفاصيل الطلب إلى هاتفك عبر تيليجرام"""
    items_text = "".join([f"▫️ {item['name']} (x{item['quantity']})\n" for item in order['items']])
    
    # رابط الخريطة
    map_link = "غير محدد"
    if order.get('location_gps') and ',' in order['location_gps']:
        lat, lng = order['location_gps'].split(',')
        map_link = f"[فتح الموقع في جوجل ماب](https://www.google.com/maps?q={lat.strip()},{lng.strip()})"

    message = (
        f"🛍️ **طلب جديد من Zubaida Beauty!**\n\n"
        f"👤 **العميل:** {order['customer_name']}\n"
        f"📞 **الهاتف:** `{order['phone']}`\n"
        f"🏙️ **المدينة:** {order['city']}\n"
        f"🏠 **العنوان:** {order['address']}\n"
        f"📍 **الموقع:** {map_link}\n\n"
        f"📋 **المنتجات:**\n{items_text}\n"
        f"💰 **الإجمالي:** {order['total_price']:,} SYP"
    )

    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    async with httpx.AsyncClient() as client:
        try:
            await client.post(url, json={
                "chat_id": CHAT_ID,
                "text": message,
                "parse_mode": "Markdown"
            })
        except Exception as e:
            print(f"Telegram Error: {e}")

# --- 1. مسارات الواجهة العامة (الزبون) ---

@app.get("/", response_class=HTMLResponse)
async def home(request: Request):
    async with httpx.AsyncClient() as client:
        p_res = await client.get(f"{SUPABASE_URL}/rest/v1/products?select=*&order=sold_count.desc&limit=3", headers=HEADERS)
        s_res = await client.get(f"{SUPABASE_URL}/rest/v1/social_accounts?is_active=eq.true", headers=HEADERS)
        h_res = await client.get(f"{SUPABASE_URL}/rest/v1/business_hours", headers=HEADERS)

    return templates.TemplateResponse("index.html", {
        "request": request,
        "products": p_res.json() if p_res.status_code == 200 else [],
        "socials": s_res.json() if s_res.status_code == 200 else [],
        "hours": h_res.json() if h_res.status_code == 200 else []
    })

@app.get("/products", response_class=HTMLResponse)
async def products_page(request: Request, category: str = "الكل", search: str = ""):
    async with httpx.AsyncClient() as client:
        c_res = await client.get(f"{SUPABASE_URL}/rest/v1/categories", headers=HEADERS)
        query = "select=*"
        if category != "الكل": query += f"&category=eq.{category}"
        if search: query += f"&name=ilike.*{search}*"
        
        p_res = await client.get(f"{SUPABASE_URL}/rest/v1/products?{query}&order=created_at.desc", headers=HEADERS)
        
        return templates.TemplateResponse("products.html", {
            "request": request,
            "products": p_res.json() if p_res.status_code == 200 else [],
            "categories": c_res.json() if c_res.status_code == 200 else [],
            "active_category": category
        })

@app.get("/products_data")
async def get_products_api(page: int = 1, limit: int = 10, category: str = "الكل", search: str = ""):
    start = (page - 1) * limit
    end = start + limit - 1
    
    async with httpx.AsyncClient() as client:
        query = f"select=*&order=created_at.desc"
        if category != "الكل":
            query += f"&category=eq.{category}"
        if search:
            query += f"&name=ilike.*{search}*"
            
        headers = {**HEADERS, "Range": f"{start}-{end}"}
        res = await client.get(f"{SUPABASE_URL}/rest/v1/products?{query}", headers=headers)
        
        # التأكد من إرجاع JSON دائماً
        if res.status_code in [200, 206]:
            return res.json()
        return []
    
@app.get("/product/{product_id}", response_class=HTMLResponse)
async def get_product_details(request: Request, product_id: int):
    async with httpx.AsyncClient() as client:
        res = await client.get(f"{SUPABASE_URL}/rest/v1/products?id=eq.{product_id}&select=*", headers=HEADERS)
        if res.status_code == 200 and res.json():
            return templates.TemplateResponse("product.html", {"request": request, "product": res.json()[0]})
    return HTMLResponse(content="المنتج غير موجود", status_code=404)

@app.get("/contact", response_class=HTMLResponse)
async def contact_page(request: Request):
    return templates.TemplateResponse("contact.html", {"request": request})

@app.post("/api/contact")
async def receive_contact_message(data: dict):
    """استقبال رسالة من صفحة اتصل بنا، حفظها، وتنبيه صاحب المتجر"""
    async with httpx.AsyncClient() as client:
        # 1. حفظ الرسالة في جدول contact_messages في Supabase
        res = await client.post(
            f"{SUPABASE_URL}/rest/v1/contact_messages",
            headers=HEADERS,
            json={
                "name": data.get('name'),
                "email": data.get('email'),
                "phone": data.get('phone'),
                "subject": data.get('subject'),
                "message": data.get('message')
            }
        )
        
        # 2. إذا نجح الحفظ، نرسل الإشعار فوراً لتيليجرام
        if res.status_code in [200, 201, 204]:
            await send_contact_notification(data)
            return {"status": "success"}
        else:
            # طباعة الخطأ في حال فشل سوبابيس للمتابعة
            print(f"Supabase Error: {res.text}")
            raise HTTPException(status_code=400, detail="فشل في حفظ الرسالة")

async def send_contact_notification(data):
    """تنسيق وإرسال رسالة التنبيه إلى بوت التيليجرام"""
    message = (
        f"📩 **رسالة تواصل جديدة!**\n\n"
        f"👤 **الاسم:** {data['name']}\n"
        f"📧 **الإيميل:** {data['email']}\n"
        f"📞 **الهاتف:** {data.get('phone', 'غير محدد')}\n"
        f"📌 **الموضوع:** {data['subject']}\n"
        f"📝 **الرسالة:**\n{data['message']}\n"
    )
    
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    async with httpx.AsyncClient() as client:
        try:
            await client.post(url, json={
                "chat_id": CHAT_ID,
                "text": message,
                "parse_mode": "Markdown"
            })
        except Exception as e:
            print(f"خطأ في إرسال إشعار تيليجرام: {e}")

@app.get("/about", response_class=HTMLResponse)
async def about_page(request: Request):
    return templates.TemplateResponse("about.html", {"request": request})

@app.get("/cart", response_class=HTMLResponse)
async def cart_page(request: Request):
    return templates.TemplateResponse("cart.html", {"request": request})

@app.get("/payment", response_class=HTMLResponse)
async def get_payment(request: Request):
    return templates.TemplateResponse("payment.html", {"request": request})

@app.post("/api/place-order")
async def place_order(order_data: dict):
    # معالجة السعر ليصبح رقماً
    clean_price = float(str(order_data['total']).replace('SYP', '').replace(',', '').strip())
    
    new_order = {
        "customer_name": order_data['customer']['name'],
        "phone": order_data['customer']['phone'],
        "city": order_data['customer']['city'],
        "address": order_data['customer']['address'],
        "location_gps": order_data['customer'].get('location', ''),
        "items": order_data['items'],
        "total_price": clean_price,
        "status": "pending"
    }

    async with httpx.AsyncClient() as client:
        res = await client.post(f"{SUPABASE_URL}/rest/v1/orders", headers=HEADERS, json=new_order)
        
        if res.status_code in [200, 201, 204]:
            # إرسال التنبيه لتيليجرام فور نجاح الحفظ
            await send_telegram_notification(new_order)
            return {"status": "success"}
        else:
            raise HTTPException(status_code=400, detail=res.text)

# --- 2. مسارات لوحة التحكم (الآدمن) ---

@app.get("/admin", response_class=HTMLResponse)
async def admin_dashboard(request: Request):
    async with httpx.AsyncClient() as client:
        p_res = await client.get(f"{SUPABASE_URL}/rest/v1/products?order=created_at.desc", headers=HEADERS)
        o_res = await client.get(f"{SUPABASE_URL}/rest/v1/orders?order=created_at.desc", headers=HEADERS)
        
        data = {
            "products": p_res.json() if p_res.status_code == 200 else [],
            "orders": o_res.json() if o_res.status_code == 200 else []
        }
    return templates.TemplateResponse("admin_dashboard.html", {"request": request, "data": data})

@app.post("/admin/save")
async def save_product(
    product_id: str = Form(None),
    name: str = Form(...),
    price: float = Form(...),
    description: str = Form(...),
    category: str = Form(...),
    stock: int = Form(50),
    image: UploadFile = File(None)
):
    async with httpx.AsyncClient() as client:
        product_data = {
            "name": name, "price": price, "description": description,
            "category": category, "stock_quantity": stock
        }

        if image and image.filename:
            file_content = await image.read()
            file_name = f"{os.urandom(4).hex()}_{image.filename}"
            storage_url = f"{SUPABASE_URL}/storage/v1/object/product-images/{file_name}"
            
            up = await client.post(storage_url, content=file_content, 
                                   headers={**HEADERS, "Content-Type": mimetypes.guess_type(file_name)[0] or "image/jpeg"})
            if up.status_code in [200, 201]:
                product_data["image_url"] = f"{SUPABASE_URL}/storage/v1/object/public/product-images/{file_name}"

        if product_id and product_id.strip():
            await client.patch(f"{SUPABASE_URL}/rest/v1/products?id=eq.{product_id}", json=product_data, headers=HEADERS)
        else:
            product_data.update({"sold_count": 0, "rating": 5.0})
            await client.post(f"{SUPABASE_URL}/rest/v1/products", json=product_data, headers=HEADERS)

    return RedirectResponse(url="/admin", status_code=303)

@app.get("/admin/delete/{product_id}")
async def delete_product(product_id: int):
    async with httpx.AsyncClient() as client:
        await client.delete(f"{SUPABASE_URL}/rest/v1/products?id=eq.{product_id}", headers=HEADERS)
    return RedirectResponse(url="/admin", status_code=303)


@app.get("/order-success", response_class=HTMLResponse)
async def order_success(request: Request, order_id: str = "ZB-NEW"):
    # هنا يمكننا مستقبلاً جلب بيانات الطلب من سوبابيس لعرضها
    # حالياً سنعرض الصفحة التي صممتها
    return templates.TemplateResponse("order-confirmation.html", {"request": request, "order_id": order_id})
