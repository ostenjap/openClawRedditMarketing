import requests
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
import re

url = 'https://www.reddit.com/r/hackathon/new/.rss'
headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'}
r = requests.get(url, headers=headers)
print("Status Code:", r.status_code)
if r.status_code != 200:
    print(r.text[:500])
    exit(1)

root = ET.fromstring(r.text)
ns = {'atom': 'http://www.w3.org/2005/Atom'}

for entry in root.findall('atom:entry', ns):
    post_id = entry.find('atom:id', ns).text.split('_')[-1]
    title = entry.find('atom:title', ns).text
    published = entry.find('atom:published', ns).text
    author_elem = entry.find('atom:author', ns)
    author = author_elem.find('atom:name', ns).text.replace('/u/', '') if author_elem is not None else '[deleted]'
    link_elem = entry.find('atom:link', ns)
    url_path = link_elem.get('href') if link_elem is not None else ''
    content_elem = entry.find('atom:content', ns)
    body = content_elem.text if content_elem is not None else ''
    body_clean = re.sub('<[^<]+?>', '', body) if body else ''
    
    print(post_id, "|", title[:30], "|", published, "|", author, "|", url_path, "|", body_clean[:50].strip().replace("\n", " "))
