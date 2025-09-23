from app.ai_agents import apply_env_from_agents
from app.services.datasheet_api import resolve_datasheet_api_first
apply_env_from_agents()
pdfs, pages = resolve_datasheet_api_first('SN74HCT240N')
print('PDFs:', pdfs)
print('Pages:', pages[:3])
