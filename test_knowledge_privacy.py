"""
test_knowledge_privacy.py -- tests for the privacy filter in knowledge.py (facts from the old staff/student sheets and clearly personal facts are hidden by query(), never deleted, never extracted).
Uses a throwaway database; the real knowledge.db is not touched. Run: python test_knowledge_privacy.py
"""
from david_config import DAVID_HOME
import sys, os, tempfile, shutil
sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, str(DAVID_HOME))

PASS = "\033[92m  PASS\033[0m"
FAIL = "\033[91m  FAIL\033[0m"
results = []


def check(name, ok, detail=""):
    results.append(bool(ok))
    print(f"{PASS if ok else FAIL}  {name}" + (f" -- {detail}" if detail else ""))


import knowledge as K

TMP = tempfile.mkdtemp(prefix="kpriv_test_")
K.DB_PATH = os.path.join(TMP, "k.db")
K.init_db()
STUDENTS = r"C:\x\فرع المدينة-الشمالي\بيانات الطلاب\قائمة الطلاب.xlsx"
STAFF = r"C:\x\فرع المدينة-المركزي\بيانات و جداول الموضفين\جداول الموظفين.xlsx"
COURSE = r"C:\x\chapter1 - all lectures\Chapter one.pdf"
BOOK = r"C:\x\books\Taleb.pdf"
rows = [("business", "Ali", "student id number", "123456", STUDENTS), ("business", "Ali", "enrolled course", "Word basics", STUDENTS), ("business", "Sara", "salary", "900", STAFF),
        ("business", "Word course", "phone number", "07700000000", COURSE), ("business", "Word course", "teaching grade level", "beginners", COURSE),
        ("business", "Word course", "default font", "Calibri is the default font", COURSE), ("books", "Black Swan", "definition", "an outlier with extreme impact", BOOK)]
with K._conn() as c:
    for d, e, p, v, s in rows:
        c.execute("INSERT INTO facts (domain,entity,predicate,value,source,created_at) VALUES (?,?,?,?,?,?)", (d, e, p, v, s, "2026-10-11"))

print("-- the rule --")
check("a source in a staff or student folder is sensitive", K.is_sensitive_source(STUDENTS) and K.is_sensitive_source(STAFF) and not K.is_sensitive_source(COURSE) and not K.is_sensitive_source(BOOK))
check("clearly personal predicates are sensitive wherever they come from (phone, student id, salary, attendance, birth date, email); course-content predicates (grade level, default font) are not",
      all(K.is_sensitive("", p) for p in ("phone number", "Student ID number", "salary", "attendance status for student", "date of birth", "email address", "WhatsApp number"))
      and not any(K.is_sensitive("", p) for p in ("teaching grade level", "default font", "definition", "release year")))

print("\n-- what David can see --")
txt = K.query("Ali student id enrolled")
check("a question about a student returns nothing from the student sheet (not the id, not even the harmless course fact: the whole source is hidden)", "123456" not in txt and "Word basics" not in txt, txt)
check("a phone number from a course document is hidden, but the same document's other facts are shown", "07700000000" not in K.query("Word course phone number") and "Calibri" in K.query("Word course default font"))
check("a salary from a staff sheet is hidden", "900" not in K.query("Sara salary"))
check("books and ordinary facts are unaffected", "outlier" in K.query("Black Swan definition"))
check("top_k is still honoured when hidden facts are skipped (the hidden ones do not take up the slots)", len([ln for ln in K.query("Word course", top_k=2).splitlines() if ln.startswith("•")]) == 2)
with K._conn() as c:
    n = c.execute("SELECT COUNT(*) FROM facts").fetchone()[0]
check("nothing was deleted: all facts are still in the database", n == len(rows), n)

print("\n-- the build skips those sources --")
src = open(str(DAVID_HOME / "knowledge.py"), encoding="utf-8").read()
check("the build loop skips chunks from sensitive sources before any LLM call", "or is_sensitive_source(source)" in src and src.index("is_sensitive_source(source)") < src.index("extract_facts(text, dom, source, chunk_id)"))
check("stats() reports how many facts are hidden", "Hidden by the privacy filter" in src)
shutil.rmtree(TMP, ignore_errors=True)

print(f"\n  {sum(results)}/{len(results)} checks passed")
sys.exit(0 if all(results) else 1)
