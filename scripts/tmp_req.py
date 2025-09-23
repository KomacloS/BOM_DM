import requests
try:
  r=requests.get("https://developer.digikey.com", timeout=10)
  print(r.status_code)
except Exception as e:
  print('err',e)
