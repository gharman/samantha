"""A deliberately healthy, short-lived workload."""

import csv
import io

rows = list(csv.DictReader(io.StringIO("name,value\na,2\nb,3\n")))
print(f"rows={len(rows)} total={sum(int(row['value']) for row in rows)}")
