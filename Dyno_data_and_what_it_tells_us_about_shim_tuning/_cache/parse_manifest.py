"""Parse thread.json: build image manifest + per-page text summaries."""
import json, os, re, collections

BASE = r"d:\Desktop\shim stacker\Dyno_data_and_what_it_tells_us_about_shim_tuning"
CACHE = os.path.join(BASE, "_cache")

with open(os.path.join(CACHE, "thread.json"), "r", encoding="utf-8") as f:
    posts = json.load(f)

print("posts:", len(posts))

# Image manifest
imgs = collections.OrderedDict()
img_re = re.compile(r"\[\[IMG:([^|\]]+)\|([^\]]+)\]\]")
link_re = re.compile(r"\[\[LINK:([^|\]]*)\|([^\]]+)\]\]")
for i, p in enumerate(posts):
    for m in img_re.finditer(p["text"]):
        alt, src = m.group(1).strip(), m.group(2).strip()
        if src not in imgs:
            imgs[src] = {"alt": alt, "first_post": i, "post_page": p["p"], "post_author": p["author"], "count": 1}
        else:
            imgs[src]["count"] += 1

print("unique images:", len(imgs))
manifest = [{"idx": k, "src": s, **v} for k, (s, v) in enumerate(imgs.items())]
with open(os.path.join(CACHE, "images_manifest.json"), "w", encoding="utf-8") as f:
    json.dump(manifest, f, indent=1, ensure_ascii=False)

# Links manifest
links = collections.OrderedDict()
for p in posts:
    for m in link_re.finditer(p["text"]):
        txt, href = m.group(1), m.group(2)
        if href not in links:
            links[href] = {"text": txt, "author": p["author"], "page": p["p"]}
print("unique links:", len(links))
with open(os.path.join(CACHE, "links_manifest.json"), "w", encoding="utf-8") as f:
    json.dump([{"href": k, **v} for k, v in links.items()], f, indent=1, ensure_ascii=False)

# Author stats
cnt = collections.Counter(p["author"] for p in posts)
print("top authors:", cnt.most_common(12))

# Clicked posts char count
cl = sum(len(p["text"]) for p in posts if p["author"] == "Clicked")
print("Clicked chars:", cl, "of", sum(len(p["text"]) for p in posts))
