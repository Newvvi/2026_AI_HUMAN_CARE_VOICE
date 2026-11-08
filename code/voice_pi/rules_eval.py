import glob, json, os, re
H = os.path.expanduser("~")
END = r"(?:주세요?|주십시오|주십쇼|줘요|줘(?!서)|주라|달라|주셔요)"
BASE = r"(?:(?:도와|살려|구해)\s*%s|사람\s*살려|119\s*(?:불러|신고|에\s*전화)|일일구\s*(?:불러|신고))" % END
R1 = r"불러\s*(?:주세요?|줘요?|주십시오)"
R2 = r"살려\s*[!.?]*\s*$"
B = r"(?:^|[\s,.!?])"
rules = {
    "기본": re.compile(B + BASE),
    "기본+불러주세요": re.compile(B + "(?:" + BASE + "|" + R1 + ")"),
    "기본+문장끝살려": re.compile(B + "(?:" + BASE + "|" + R2 + ")"),
    "기본+둘다": re.compile(B + "(?:" + BASE + "|" + R1 + "|" + R2 + ")"),
}
ld = lambda f: [json.loads(l)["text"] for l in open(f, encoding="utf-8")] if os.path.exists(f) else None
sets = {"tiny": [f"{H}/nn_n{i}_windows.jsonl" for i in (1, 2, 3)],
        "base": [f"{H}/mm_base_n{i}_windows.jsonl" for i in (1, 2, 3)],
        "small": [f"{H}/mm_small_n{i}_windows.jsonl" for i in (1, 2, 3)]}
print("[새 문장 재현율: 불러주세요(119) / 사람 살려 / 살려주세요]")
for m, fs in sets.items():
    t = [ld(f) for f in fs]
    if any(x is None for x in t):
        continue
    for name, rx in rules.items():
        print(f"  {m:5} {name:14} " + " / ".join(f"{sum(bool(rx.search(x)) for x in tx)}/{len(tx)}" for tx in t))
e2 = {m: ld(f"{H}/mm_{m}_e2_windows.jsonl") for m in ("base", "small")}
for m, tx in e2.items():
    if tx:
        print(f"  [{m} 도와주세요(M11)] 기본 {sum(bool(rules['기본'].search(x)) for x in tx)}/{len(tx)}")
c = ld(f"{H}/run_c5_windows.jsonl") or []
oth = [x for f in sorted(glob.glob(f"{H}/oo_o*_windows.jsonl")) for x in ld(f)]
print(f"\n[오경보: C 3시간 {len(c)}구간, 다른 녹음 {len(oth)}구간 (tiny 기준)]")
for name, rx in rules.items():
    hc = [x for x in c if rx.search(x)]; ho = [x for x in oth if rx.search(x)]
    print(f"  {name:14} C {len(hc)}개, 다른 녹음 {len(ho)}개")
    if name != "기본":
        for x in (hc + ho)[:8]:
            print("      ->", x[:50])
