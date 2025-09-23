import os
os.environ['BOM_HEADLESS_MOUSER']='1'
from app.services.datasheet_api import mouser_candidate_urls
from app.ai_agents import apply_env_from_agents
from app.services.datasheet_html import find_pdfs_in_page

apply_env_from_agents()

mpn='SN74HCT240N'
pdfs,pages = mouser_candidate_urls(mpn, api_key=(os.getenv('MOUSER_API_KEY') or os.getenv('PROVIDER_MOUSER_KEY') or ''))
print('pages', pages)

for u in pages[:2]:
    try:
        links = find_pdfs_in_page(u, mpn, 'Texas Instruments')
        print(u, '->', len(links), 'links')
        for l in links[:5]:
            print('  ', l)
    except Exception as e:
        print('ERR', u, e)
