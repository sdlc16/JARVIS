"""Ventana de texto NEUTRA (tkinter, sin autocorreccion) usada por selftest.py:
como destino exacto para verificar el tecleo Unicode del puente."""

import sys
import time
import tkinter as tk

out = sys.argv[1]
root = tk.Tk()
root.title("JarvisTypeTarget")
root.geometry("500x200+300+300")
t = tk.Text(root)
t.pack(fill="both", expand=True)
t.focus_force()
last = [time.time()]
t.bind("<Key>", lambda e: last.__setitem__(0, time.time()))


def tick():
    if time.time() - last[0] > 2.5 and t.get("1.0", "end-1c"):
        open(out, "w", encoding="utf-8").write(t.get("1.0", "end-1c"))
        root.destroy()
        return
    if time.time() - last[0] > 20:
        root.destroy()
        return
    root.after(100, tick)


root.after(100, tick)
root.mainloop()
