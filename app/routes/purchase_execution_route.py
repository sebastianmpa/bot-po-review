from fastapi import APIRouter
from controllers.purchase_execution_controller import router as purchase_execution_router

router = APIRouter()
router.include_router(purchase_execution_router, prefix="/purchase-execution", tags=["Purchase Execution"])
