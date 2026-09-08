import tkinter as tk
from tkinter import ttk
import numpy as np
import json
from scipy.optimize import fsolve, minimize
import matplotlib.pyplot as plt
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg

# ==============================
# 物理模型
# ==============================

class ShockModel:

    def __init__(self):

        self.Ap = 3.14e-4
        self.Cd = 0.7
        self.rho = 850
        self.ro = 0.015
        self.rm = 0.012
        self.Ao = 1e-6
        self.E = 2.1e11

    def stack_stiffness(self, stack):

        k = 0

        for shim in stack:

            t = shim["t"]

            k += self.E * t**3

        return k

    def force_single_velocity(self, v, stack):

        k = self.stack_stiffness(stack)

        def equation(P):

            delta = P * self.rm**4 / k

            Agap = 2*np.pi*self.ro*delta

            Q = self.Cd*np.sqrt(2*P/self.rho)*(self.Ao + Agap)

            return self.Ap*v - Q

        try:
            P = fsolve(equation, 1e5)[0]
        except:
            P = 0

        return P*self.Ap

    def simulate_curve(self, stack):

        velocities = np.linspace(0.01, 2, 60)

        forces = []

        for v in velocities:

            f = self.force_single_velocity(v, stack)

            forces.append(f)

        return velocities, np.array(forces)


# ==============================
# 阀片优化
# ==============================

class StackOptimizer:

    def __init__(self, model):

        self.model = model

        self.diameters = [32,30,28,26]

    def build_stack(self, thickness):

        stack = []

        for t,d in zip(thickness,self.diameters):

            stack.append({
                "t":t/1000,
                "d":d
            })

        return stack

    def loss(self, x, target_v, target_f):

        stack = self.build_stack(x)

        v, f = self.model.simulate_curve(stack)

        return np.mean((f-target_f)**2)

    def optimize(self, target_v, target_f):

        x0 = [0.15,0.15,0.10,0.10]

        bounds = [(0.05,0.3)]*4

        res = minimize(self.loss,x0,args=(target_v,target_f),bounds=bounds)

        return self.build_stack(res.x)


# ==============================
# 曲线拖动
# ==============================

class CurveEditor:

    def __init__(self, ax, update_callback):

        self.ax = ax
        self.points = None
        self.selected = None
        self.callback = update_callback

        self.cid1 = ax.figure.canvas.mpl_connect("button_press_event",self.on_press)
        self.cid2 = ax.figure.canvas.mpl_connect("motion_notify_event",self.on_motion)
        self.cid3 = ax.figure.canvas.mpl_connect("button_release_event",self.on_release)

    def set_points(self, line):

        self.points = line

    def on_press(self,event):

        if self.points is None:
            return

        x = self.points.get_xdata()
        y = self.points.get_ydata()

        d = np.sqrt((x-event.xdata)**2+(y-event.ydata)**2)

        idx = np.argmin(d)

        if d[idx] < 20:

            self.selected = idx

    def on_motion(self,event):

        if self.selected is None:
            return

        y = self.points.get_ydata()

        y[self.selected] = max(event.ydata,0)

        self.points.set_ydata(y)

        self.ax.figure.canvas.draw()

        self.callback()

    def on_release(self,event):

        self.selected=None


# ==============================
# GUI
# ==============================

class ShockGUI:

    def __init__(self,root):

        self.root=root

        root.title("Shock Valve Simulator")

        self.model=ShockModel()

        self.optimizer=StackOptimizer(self.model)

        self.stack=[
            {"t":0.15/1000,"d":32},
            {"t":0.15/1000,"d":30},
            {"t":0.10/1000,"d":28},
            {"t":0.10/1000,"d":26},
        ]

        self.build_gui()

        self.simulate()

    def build_gui(self):

        left=tk.Frame(self.root)
        left.pack(side="left",fill="y")

        right=tk.Frame(self.root)
        right.pack(side="right",fill="both",expand=True)

        self.entries=[]

        tk.Label(left,text="Shim Thickness (mm)").pack()

        for i in range(4):

            e=tk.Entry(left)

            e.insert(0,str(self.stack[i]["t"]*1000))

            e.pack()

            self.entries.append(e)

        tk.Button(left,text="Simulate",command=self.simulate).pack(pady=5)

        tk.Button(left,text="Optimize Stack",command=self.optimize).pack(pady=5)

        tk.Button(left,text="Save Stack",command=self.save_stack).pack(pady=5)

        fig,ax=plt.subplots()

        self.ax=ax

        ax.set_xlabel("Velocity m/s")

        ax.set_ylabel("Force N")

        self.canvas=FigureCanvasTkAgg(fig,master=right)

        self.canvas.get_tk_widget().pack(fill="both",expand=True)

        self.editor=CurveEditor(ax,self.target_updated)

    def read_stack(self):

        stack=[]

        diam=[32,30,28,26]

        for e,d in zip(self.entries,diam):

            t=float(e.get())/1000

            stack.append({"t":t,"d":d})

        return stack

    def simulate(self):

        self.stack=self.read_stack()

        v,f=self.model.simulate_curve(self.stack)

        self.v=v

        self.f=f

        self.ax.clear()

        self.ax.set_xlabel("Velocity")

        self.ax.set_ylabel("Force")

        self.line_sim,=self.ax.plot(v,f,label="Simulated")

        self.line_target,=self.ax.plot(v,f,"ro",label="Target")

        self.ax.legend()

        self.canvas.draw()

        self.editor.set_points(self.line_target)

    def target_updated(self):

        self.target_v=self.line_target.get_xdata()

        self.target_f=self.line_target.get_ydata()

    def optimize(self):

        target_v=self.line_target.get_xdata()

        target_f=self.line_target.get_ydata()

        new_stack=self.optimizer.optimize(target_v,target_f)

        for e,s in zip(self.entries,new_stack):

            e.delete(0,"end")

            e.insert(0,str(round(s["t"]*1000,3)))

        self.simulate()

    def save_stack(self):

        stack=self.read_stack()

        with open("stack.json","w") as f:

            json.dump(stack,f,indent=2)


# ==============================
# 启动
# ==============================

if __name__=="__main__":

    root=tk.Tk()

    app=ShockGUI(root)

    root.mainloop()