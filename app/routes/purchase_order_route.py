from fastapi import APIRouter
from controllers.purchase_order_controller import router as purchase_order_router

router = APIRouter()
router.include_router(purchase_order_router, prefix="/purchase-order", tags=["Purchase Orders"])
