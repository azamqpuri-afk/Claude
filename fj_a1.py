import pickle, re
d = r"C:\Users\DBSERVER\AppData\Local\Temp\claude\C--Users-DBSERVER\28bb1713-1a7e-4080-a861-91cc5051d564\scratchpad"
rows, ach = pickle.load(open(d+r"\fj_lines.pkl","rb"))
pat = re.compile(r"AZAM|DRAW|HINA|RASHEED|QAYYUM|^DF|PARTNER|DOST|HOME|FAMILY|CAPITAL|SUSPENSE|ADJUST|CLEARING|PERSONAL", re.I)
for a in ach:
    if a['an'] and pat.search(a['an']): print(a['e'], a['ac'], a['an'], a['lev'], a['hc'])
