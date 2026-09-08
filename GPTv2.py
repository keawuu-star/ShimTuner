import tkinter as tk
from tkinter import ttk
import numpy as np
import random
from scipy.optimize import fsolve
import matplotlib.pyplot as plt
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
from matplotlib.animation import FuncAnimation

# =========================================================
# 阀片数据库
# =========================================================

class ShimDatabase:

    def __init__(self):

        self.diameters=[32,30,28,26,24,22]

        self.thickness=[0.10,0.12,0.15,0.20,0.25]

    def random_stack(self,n=4):

        stack=[]

        for i in range(n):

            stack.append({
                "d":self.diameters[i],
                "t":random.choice(self.thickness)/1000
            })

        return stack

# =========================================================
# 物理模型
# =========================================================

class ShockPhysics:

    def __init__(self):

        self.Ap=3.14e-4
        self.rho=850
        self.Cd=0.7

        self.ro=0.015
        self.rm=0.012

        self.Ao=1e-6

        self.E=2.1e11
        self.nu=0.3

        self.contact_deflection=0.0003

    # 阀片刚度
    def stack_stiffness(self,stack):

        k=0

        for shim in stack:

            t=shim["t"]

            r=shim["d"]/2000

            rm=r*0.8

            k_plate=(16*self.E*t**3)/(3*(1-self.nu**2)*rm**4)

            k+=k_plate

        return k

    # 阀片接触模型
    def shim_gap(self,P,k):

        delta=P/k

        if delta>self.contact_deflection:

            delta=self.contact_deflection+(delta-self.contact_deflection)*0.2

        return 2*np.pi*self.ro*delta

    # 单速度求力
    def force(self,v,stack):

        k=self.stack_stiffness(stack)

        def eq(P):

            Agap=self.shim_gap(P,k)

            Q=self.Cd*np.sqrt(2*P/self.rho)*(self.Ao+Agap)

            return self.Ap*abs(v)-Q

        try:

            P=fsolve(eq,1e5)[0]

        except:

            P=0

        return np.sign(v)*P*self.Ap

    # 双向阻尼
    def simulate(self,stack_c,stack_r):

        v=np.linspace(-2,2,120)

        f=[]

        for vel in v:

            if vel>=0:

                f.append(self.force(vel,stack_c))

            else:

                f.append(self.force(vel,stack_r))

        return v,np.array(f)

# =========================================================
# 遗传算法优化
# =========================================================

class GeneticOptimizer:

    def __init__(self,physics,database):

        self.physics=physics

        self.database=database

        self.population_size=40
        self.generations=40

    def fitness(self,stack,target_v,target_f):

        v,f=self.physics.simulate(stack,stack)

        return np.mean((f-target_f)**2)

    def mutate(self,stack):

        s=[dict(x) for x in stack]

        i=random.randint(0,len(s)-1)

        s[i]["t"]=random.choice(self.database.thickness)/1000

        return s

    def crossover(self,a,b):

        c=[]

        for x,y in zip(a,b):

            if random.random()<0.5:

                c.append(dict(x))

            else:

                c.append(dict(y))

        return c

    def optimize(self,target_v,target_f):

        pop=[self.database.random_stack() for _ in range(self.population_size)]

        for g in range(self.generations):

            scores=[self.fitness(p,target_v,target_f) for p in pop]

            ranked=[x for _,x in sorted(zip(scores,pop),key=lambda x:x[0])]

            pop=ranked[:10]

            while len(pop)<self.population_size:

                a=random.choice(ranked[:10])

                b=random.choice(ranked[:10])

                c=self.crossover(a,b)

                if random.random()<0.3:

                    c=self.mutate(c)

                pop.append(c)

        return pop[0]

# =========================================================
# 曲线拖动
# =========================================================

class CurveEditor:

    def __init__(self,ax,callback):

        self.ax=ax
        self.callback=callback
        self.line=None
        self.selected=None

        ax.figure.canvas.mpl_connect("button_press_event",self.press)
        ax.figure.canvas.mpl_connect("motion_notify_event",self.motion)
        ax.figure.canvas.mpl_connect("button_release_event",self.release)

    def set_line(self,line):

        self.line=line

    def press(self,event):

        if self.line is None:
            return

        x=self.line.get_xdata()
        y=self.line.get_ydata()

        d=np.sqrt((x-event.xdata)**2+(y-event.ydata)**2)

        idx=np.argmin(d)

        if d[idx]<50:

            self.selected=idx

    def motion(self,event):

        if self.selected is None:
            return

        y=self.line.get_ydata()

        y[self.selected]=event.ydata

        self.line.set_ydata(y)

        self.ax.figure.canvas.draw()

        self.callback()

    def release(self,event):

        self.selected=None

# =========================================================
# GUI
# =========================================================

class ShockGUI:

    def __init__(self,root):

        self.root=root

        root.title("Shock Tuner")

        self.database=ShimDatabase()

        self.physics=ShockPhysics()

        self.optimizer=GeneticOptimizer(self.physics,self.database)

        self.stack_c=self.database.random_stack()

        self.stack_r=self.database.random_stack()

        self.build()

        self.update_curve()

    def build(self):

        left=tk.Frame(self.root)
        left.pack(side="left",fill="y")

        right=tk.Frame(self.root)
        right.pack(side="right",fill="both",expand=True)

        tk.Button(left,text="Optimize",command=self.optimize).pack(fill="x")

        tk.Button(left,text="Random Stack",command=self.random_stack).pack(fill="x")

        fig,ax=plt.subplots()

        self.ax=ax

        ax.set_xlabel("Velocity")
        ax.set_ylabel("Force")

        self.canvas=FigureCanvasTkAgg(fig,right)

        self.canvas.get_tk_widget().pack(fill="both",expand=True)

        self.editor=CurveEditor(ax,self.target_changed)

    def update_curve(self):

        v,f=self.physics.simulate(self.stack_c,self.stack_r)

        self.v=v
        self.f=f

        self.ax.clear()

        self.line_sim,=self.ax.plot(v,f,label="Simulated")

        self.line_target,=self.ax.plot(v,f,"ro",label="Target")

        self.ax.legend()

        self.canvas.draw()

        self.editor.set_line(self.line_target)

    def target_changed(self):

        self.target_v=self.line_target.get_xdata()
        self.target_f=self.line_target.get_ydata()

    def random_stack(self):

        self.stack_c=self.database.random_stack()

        self.stack_r=self.database.random_stack()

        self.update_curve()

    def optimize(self):

        target_v=self.line_target.get_xdata()
        target_f=self.line_target.get_ydata()

        stack=self.optimizer.optimize(target_v,target_f)

        self.stack_c=stack
        self.stack_r=stack

        self.update_curve()

# =========================================================
# 主程序
# =========================================================

root=tk.Tk()

app=ShockGUI(root)

root.mainloop()