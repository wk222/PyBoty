import requests
import json

def fetch_bili_trending():
    url = "https://api.bilibili.com/x/web-interface/popular?ps=20&pn=1"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36",
        "Referer": "https://www.bilibili.com/"
    }
    try:
        response = requests.get(url, headers=headers, timeout=10)
        data = response.json()
        if data.get("code") == 0:
            list_data = data.get("data", {}).get("list", [])
            results = []
            for item in list_data:
                results.append({
                    "title": item.get("title"),
                    "author": item.get("owner", {}).get("name"),
                    "view": item.get("stat", {}).get("view"),
                    "like": item.get("stat", {}).get("like"),
                    "danmaku": item.get("stat", {}).get("danmaku"),
                    "desc": item.get("desc"),
                    "pic": item.get("pic"),
                    "url": f"https://www.bilibili.com/video/{item.get('bvid')}"
                })
            return results
        else:
            return {"error": f"Bilibili API error: {data.get('message')}"}
    except Exception as e:
        return {"error": str(e)}

print(json.dumps(fetch_bili_trending(), ensure_ascii=False))
