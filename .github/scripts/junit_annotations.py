"""Turn pytest JUnit XML failures into GitHub error annotations.

Annotations show up on the PR and through the checks API, so a failing test is
readable without opening the raw job log.
"""

import sys
import xml.etree.ElementTree as ET


def main(path: str) -> int:
    try:
        root = ET.parse(path).getroot()
    except (OSError, ET.ParseError) as exc:
        print(f"::warning::no JUnit report to annotate ({exc})")
        return 0
    count = 0
    for case in root.iter("testcase"):
        for kind in ("failure", "error"):
            node = case.find(kind)
            if node is None:
                continue
            name = f"{case.get('classname', '')}::{case.get('name', '')}"
            logs = "\n".join((case.findtext(t) or "") for t in ("system-out", "system-err")).strip()
            detail = (node.text or node.get("message") or "").strip()[-1200:]
            if logs:
                detail += "\n--- captured output ---\n" + logs[-2500:]
            detail = detail.replace("%", "%25").replace("\r", "").replace("\n", "%0A")
            print(f"::error title={kind}: {name}::{detail}")
            count += 1
    print(f"{count} failing test(s) annotated")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else "report.xml"))
