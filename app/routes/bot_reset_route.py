import subprocess
from fastapi import HTTPException, APIRouter
from models.bot_reset_model import ResetResponse

# Constante de bots
PM2_BOTS = [
    "api-seo"
]

router = APIRouter()

@router.post("/bot-reset", response_model=ResetResponse)
def bot_reset():
    """
    Reinicia el bot api-seo usando PM2
    """
    for bot_name in PM2_BOTS:
        try:
            subprocess.run(["pm2", "restart", bot_name], check=True)
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Error reiniciando {bot_name}: {str(e)}")
    return {"status": "OK"}