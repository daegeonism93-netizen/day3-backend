import os
from typing import Optional

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from supabase import create_client

load_dotenv()  # 로컬에서는 .env, Railway에서는 대시보드 Variables 사용

SUPABASE_URL = os.getenv("SUPABASE_URL", "")
SUPABASE_KEY = os.getenv("SUPABASE_KEY", "")
# 허용할 프론트 주소 (쉼표로 여러 개). 배포 시에는 Vercel 주소를 넣는다.
FRONT_ORIGIN = os.getenv("FRONT_ORIGIN", "http://localhost:5500,http://127.0.0.1:5500")

FREE_FROM = 30000   # 이 금액 이상이면 배송비 무료
SHIPPING_FEE = 3000

app = FastAPI(title="day3 backend")

app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in FRONT_ORIGIN.split(",") if o.strip()],
    allow_methods=["*"],
    allow_headers=["*"],
)

_db = None


def db():
    """Supabase 클라이언트 (처음 사용할 때 생성)."""
    global _db
    if _db is None:
        if not SUPABASE_URL or not SUPABASE_KEY:
            raise HTTPException(500, "SUPABASE_URL / SUPABASE_KEY 환경변수가 없습니다.")
        _db = create_client(SUPABASE_URL, SUPABASE_KEY)
    return _db


class AddItem(BaseModel):
    product_id: int
    quantity: int = Field(1, ge=1, le=99)


class SetQty(BaseModel):
    quantity: int = Field(..., ge=0, le=99)


def build_cart(cart_id: str) -> dict:
    rows = (
        db().table("cart_items")
        .select("quantity, products(id, name, category, emoji, price)")
        .eq("cart_id", cart_id)
        .order("product_id")
        .execute()
        .data
    )
    items = []
    for r in rows:
        p = r["products"]
        items.append({**p, "quantity": r["quantity"]})
    total_qty = sum(i["quantity"] for i in items)
    subtotal = sum(i["price"] * i["quantity"] for i in items)
    shipping = 0 if (not items or subtotal >= FREE_FROM) else SHIPPING_FEE
    return {
        "items": items,
        "total_qty": total_qty,
        "subtotal": subtotal,
        "shipping": shipping,
        "total": subtotal + shipping,
    }


# ── 상태 확인 ──
@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/health/db")
def health_db():
    try:
        db().table("products").select("id").limit(1).execute()
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, f"DB 오류: {e}")
    return {"status": "ok"}


# ── 상품 ──
@app.get("/products")
def products():
    return db().table("products").select("*").order("id").execute().data


# ── 장바구니 (로그인 대신 브라우저가 만든 cart_id 사용) ──
@app.get("/cart/{cart_id}")
def get_cart(cart_id: str):
    return build_cart(cart_id)


@app.post("/cart/{cart_id}/items")
def add_item(cart_id: str, body: AddItem):
    found = db().table("products").select("id").eq("id", body.product_id).execute().data
    if not found:
        raise HTTPException(404, "없는 상품입니다.")
    cur = (
        db().table("cart_items").select("quantity")
        .eq("cart_id", cart_id).eq("product_id", body.product_id)
        .execute().data
    )
    qty = min(99, (cur[0]["quantity"] if cur else 0) + body.quantity)
    db().table("cart_items").upsert(
        {"cart_id": cart_id, "product_id": body.product_id, "quantity": qty},
        on_conflict="cart_id,product_id",
    ).execute()
    return build_cart(cart_id)


@app.patch("/cart/{cart_id}/items/{product_id}")
def set_qty(cart_id: str, product_id: int, body: SetQty):
    q = db().table("cart_items").delete().eq("cart_id", cart_id).eq("product_id", product_id)
    if body.quantity == 0:
        q.execute()
    else:
        db().table("cart_items").upsert(
            {"cart_id": cart_id, "product_id": product_id, "quantity": body.quantity},
            on_conflict="cart_id,product_id",
        ).execute()
    return build_cart(cart_id)


@app.delete("/cart/{cart_id}/items/{product_id}")
def remove_item(cart_id: str, product_id: int):
    db().table("cart_items").delete().eq("cart_id", cart_id).eq("product_id", product_id).execute()
    return build_cart(cart_id)


@app.delete("/cart/{cart_id}")
def clear_cart(cart_id: str):
    db().table("cart_items").delete().eq("cart_id", cart_id).execute()
    return build_cart(cart_id)
