import numpy as np
import tkinter as tk
from dataclasses import dataclass
from tkinter import ttk, messagebox
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
import matplotlib.pyplot as plt
from scipy.optimize import minimize
from scipy.interpolate import interp1d


@dataclass
class ShimPlate:
    d_in_mm: float
    d_out_mm: float
    thickness_mm: float
    elastic_modulus: float = 200e9
    poisson_ratio: float = 0.30

    @property
    def inner_radius_m(self):
        return (max(self.d_in_mm, 0.0) * 1e-3) / 2.0

    @property
    def outer_radius_m(self):
        return (max(self.d_out_mm, 0.0) * 1e-3) / 2.0

    @property
    def annular_area_m2(self):
        return np.pi * (self.outer_radius_m**2 - self.inner_radius_m**2)

    def _phi_coeff(self):
        """无量纲 Φ(a/b, ν) —— 与 shim_physics_core.analytic_phi 同一线性方程组.

        系数矩阵 [[2ρ², 1], [2(1+ν), ν−1]]; 这里用 Cramer 法则显式求解
        (a11 = 2ρ², a21 = 2(1+ν), 二者不可混用).
        """
        a = self.inner_radius_m
        b = self.outer_radius_m
        if b <= 0.0 or a <= 0.0 or a >= b:
            return 0.0

        rho = a / b
        L = np.log(rho)
        nu = self.poisson_ratio
        a11 = 2.0 * (rho ** 2)
        a21 = 2.0 * (1.0 + nu)
        rhs1 = (rho ** 2 / 8.0) * (2.0 * L + 1.0) - (rho ** 4) / 16.0
        rhs2 = (3.0 + nu) / 16.0
        det = a11 * (nu - 1.0) - a21
        if abs(det) < 1e-12:
            return 0.0

        B = (rhs1 * (nu - 1.0) - rhs2) / det
        C = (a11 * rhs2 - a21 * rhs1) / det
        phi = B * (1.0 - rho ** 2) - C * L + (rho ** 2 / 8.0) * L + (1.0 - rho ** 4) / 64.0
        return float(phi)

    def bending_stiffness(self):
        h = max(self.thickness_mm, 1e-6) * 1e-3
        return self.elastic_modulus * (h ** 3) / (12.0 * (1.0 - self.poisson_ratio ** 2))

    def deflection_under_pressure(self, pressure_pa):
        a = self.inner_radius_m
        b = self.outer_radius_m
        if b <= 0.0 or a <= 0.0 or a >= b:
            return 0.0

        phi = self._phi_coeff()
        if phi <= 0.0:
            return 0.0

        D = self.bending_stiffness()
        return float(phi * pressure_pa * (b ** 4) / D)

    def effective_stiffness(self, pressure_pa=1e6):
        displacement = self.deflection_under_pressure(pressure_pa)
        if displacement <= 0.0:
            return np.inf
        return (pressure_pa * self.annular_area_m2) / displacement


# ==========================================
# 1. 物理模型核心: 用环形薄板理论构建可求解的阀片刚度模型
# ==========================================
class DamperModel:
    def __init__(self, bleed_area, shims, cSt=15.0, rho=850.0):
        self.bleed_area = bleed_area
        self.shims = shims
        self.rho = rho
        self.cSt = cSt
        self.Cd = 0.65
        self.piston_area = 1000  # mm2
        self.port_diameter = 10  # mm
        self.bleed_length = 5.0  # mm
        self.material_E = 200e9
        self.material_nu = 0.30

    def _plate_for_shim(self, shim, thickness_override=None):
        t = thickness_override if thickness_override is not None else shim['t']
        return ShimPlate(
            d_in_mm=float(shim['d_in']),
            d_out_mm=float(shim['d_out']),
            thickness_mm=float(t),
            elastic_modulus=self.material_E,
            poisson_ratio=self.material_nu,
        )

    def _effective_stack_stiffness(self, pressure_pa, thicknesses=None):
        shims = self.shims
        if thicknesses is not None:
            if len(thicknesses) != len(self.shims):
                raise ValueError("thicknesses length must match shims length")
            shims = []
            for shim, t in zip(self.shims, thicknesses):
                shim_copy = dict(shim)
                shim_copy['t'] = float(t)
                shims.append(shim_copy)

        if not shims:
            return 1e9

        max_outer = max(float(s['d_out']) for s in shims)
        min_inner = min(float(s['d_in']) for s in shims)
        k_total = 0.0

        for shim in shims:
            plate = self._plate_for_shim(shim)
            k_i = plate.effective_stiffness(pressure_pa)
            if np.isinf(k_i):
                continue

            overlap = max(0.0, min(float(shim['d_out']), max_outer) - max(float(shim['d_in']), min_inner))
            active_ratio = overlap / max(max_outer - min_inner, 1e-9)
            k_total += k_i * max(active_ratio, 0.05)

        return max(k_total, 1e3)

    def get_fv_curve(self, velocities, thicknesses=None, cSt=None):
        current_cSt = cSt if cSt is not None else self.cSt
        mu = current_cSt * self.rho * 1e-6

        delta_p = np.linspace(0.0, 20e6, 600)

        # 低速流量: 层流 H-P
        d_bleed = np.sqrt(4.0 * self.bleed_area / np.pi) * 1e-3
        L = self.bleed_length * 1e-3
        q_bleed = (delta_p * np.pi * d_bleed ** 4) / (128.0 * mu * L)

        # 阀片堆叠刚度: 采用环形薄板理论的有效刚度
        k_eq = np.array([
            self._effective_stack_stiffness(p, thicknesses) if (self.shims or thicknesses is not None) else 1e9
            for p in delta_p
        ])

        A_port_m2 = (np.pi * (self.port_diameter * 1e-3) ** 2) / 4.0
        deflection = (delta_p * A_port_m2) / k_eq
        deflection = np.clip(deflection, 0.0, 1e-3)

        A_shim_m2 = np.pi * (self.port_diameter * 1e-3) * deflection
        q_shim = self.Cd * A_shim_m2 * np.sqrt(2.0 * delta_p / self.rho)

        Q_total = q_bleed + q_shim
        piston_area_m2 = self.piston_area * 1e-6
        v_calc = Q_total / piston_area_m2
        f_calc = delta_p * piston_area_m2

        v_calc, idx = np.unique(v_calc, return_index=True)
        f_calc = f_calc[idx]

        interp_func = interp1d(v_calc, f_calc, kind='linear', fill_value='extrapolate')
        return interp_func(np.asarray(velocities))

# ==========================================
# 2. GUI 界面
# ==========================================
class DamperApp:
    def __init__(self, root):
        self.root = root
        self.root.title("高级减震器阀片模拟器 (含粘度模型)")
        self.root.geometry("1300x750")
        
        self.velocities = np.arange(0, 10.5, 0.5)
        self.model = DamperModel(bleed_area=2.5, shims=[])
        self.shim_rows = [] 
        
        self.setup_ui()
        self.load_default_shims()
        self.simulate_current()

    def setup_ui(self):
        # 左侧面板
        self.panel_left = ttk.Frame(self.root, width=400, padding=10)
        self.panel_left.pack(side=tk.LEFT, fill=tk.Y)
        
        # 流体参数设置
        frame_fluid = ttk.LabelFrame(self.panel_left, text="Fluid Properties (液体特性)", padding=5)
        frame_fluid.pack(fill=tk.X, pady=5)
        
        ttk.Label(frame_fluid, text="Viscosity (运动粘度 cSt):").grid(row=0, column=0, sticky='w')
        self.entry_cst = ttk.Entry(frame_fluid, width=10)
        self.entry_cst.insert(0, "15.0")
        self.entry_cst.grid(row=0, column=1, padx=5, pady=2)
        
        ttk.Label(frame_fluid, text="Density (密度 kg/m³):").grid(row=1, column=0, sticky='w')
        self.entry_rho = ttk.Entry(frame_fluid, width=10)
        self.entry_rho.insert(0, "850")
        self.entry_rho.grid(row=1, column=1, padx=5, pady=2)

        # 溢流口设置
        frame_bleed = ttk.LabelFrame(self.panel_left, text="Bleed Port (溢流口)", padding=5)
        frame_bleed.pack(fill=tk.X, pady=5)
        ttk.Label(frame_bleed, text="Area (mm²):").pack(side=tk.LEFT)
        self.entry_bleed = ttk.Entry(frame_bleed, width=10)
        self.entry_bleed.insert(0, "2.5")
        self.entry_bleed.pack(side=tk.LEFT, padx=5)
        
        # 阀片列表
        self.frame_shims_container = ttk.LabelFrame(self.panel_left, text="Shim Stack (从靠近活塞侧开始)", padding=5)
        self.frame_shims_container.pack(fill=tk.BOTH, expand=True, pady=5)
        
        header = ttk.Frame(self.frame_shims_container)
        header.pack(fill=tk.X)
        for t in ["d_in", "d_out", "thickness"]:
            ttk.Label(header, text=t, width=10).pack(side=tk.LEFT, padx=2)
        
        self.frame_shims = ttk.Frame(self.frame_shims_container)
        self.frame_shims.pack(fill=tk.BOTH, expand=True)
        
        # 按钮
        ttk.Button(self.panel_left, text="+ Add Shim", command=self.add_shim_row).pack(fill=tk.X)
        ttk.Button(self.panel_left, text="▶ Simulate Current (模拟当前)", command=self.simulate_current).pack(fill=tk.X, pady=2)
        ttk.Button(self.panel_left, text="★ Reverse Optimize (逆向优化厚度)", command=self.run_optimization).pack(fill=tk.X, pady=10)
        
        # 右侧绘图
        self.panel_right = ttk.Frame(self.root)
        self.panel_right.pack(side=tk.RIGHT, fill=tk.BOTH, expand=True)
        self.fig, self.ax = plt.subplots(figsize=(8, 6))
        self.canvas = FigureCanvasTkAgg(self.fig, master=self.panel_right)
        self.canvas.get_tk_widget().pack(fill=tk.BOTH, expand=True)
        
        self._dragging_point = None
        self.target_forces = np.zeros_like(self.velocities)
        self.canvas.mpl_connect('button_press_event', self.on_press)
        self.canvas.mpl_connect('motion_notify_event', self.on_motion)
        self.canvas.mpl_connect('button_release_event', self.on_release)

    def add_shim_row(self, din=8, dout=22, t=0.2):
        row = ttk.Frame(self.frame_shims)
        row.pack(fill=tk.X, pady=1)
        e1 = ttk.Entry(row, width=8); e1.insert(0, str(din)); e1.pack(side=tk.LEFT, padx=2)
        e2 = ttk.Entry(row, width=8); e2.insert(0, str(dout)); e2.pack(side=tk.LEFT, padx=2)
        e3 = ttk.Entry(row, width=8); e3.insert(0, str(t)); e3.pack(side=tk.LEFT, padx=2)
        ttk.Button(row, text="X", width=3, command=lambda r=row: self.del_row(r)).pack(side=tk.LEFT)
        self.shim_rows.append({'f': row, 'd_in': e1, 'd_out': e2, 't': e3})

    def del_row(self, r):
        for item in self.shim_rows:
            if item['f'] == r:
                item['f'].destroy()
                self.shim_rows.remove(item)
                break

    def get_inputs(self):
        try:
            return {
                'cSt': float(self.entry_cst.get()),
                'rho': float(self.entry_rho.get()),
                'bleed': float(self.entry_bleed.get()),
                'shims': [{'d_in': float(r['d_in'].get()), 'd_out': float(r['d_out'].get()), 't': float(r['t'].get())} for r in self.shim_rows]
            }
        except:
            messagebox.showerror("Error", "输入格式有误"); return None

    def simulate_current(self):
        data = self.get_inputs()
        if not data: return
        self.model.cSt, self.model.rho, self.model.bleed_area, self.model.shims = data['cSt'], data['rho'], data['bleed'], data['shims']
        self.current_forces = self.model.get_fv_curve(self.velocities)
        self.target_forces = self.current_forces.copy()
        self.update_plot()

    def update_plot(self):
        self.ax.clear()
        self.ax.set_title(f"F-v Curve (Oil: {self.model.cSt} cSt)", fontsize=12)
        self.ax.set_xlabel("Velocity (m/s)"); self.ax.set_ylabel("Force (N)"); self.ax.grid(True)
        self.ax.plot(self.velocities, self.current_forces, 'b--', label='Current Sim')
        self.target_line, = self.ax.plot(self.velocities, self.target_forces, 'ro-', label='Target (Drag Me)')
        self.ax.set_ylim(0, max(self.current_forces)*2 + 100)
        self.ax.legend(); self.canvas.draw()

    def on_press(self, e):
        if e.inaxes != self.ax: return
        dist = np.abs(self.velocities - e.xdata)
        idx = np.argmin(dist)
        if dist[idx] < 0.3: self._dragging_point = idx

    def on_motion(self, e):
        if self._dragging_point is not None and e.inaxes == self.ax:
            self.target_forces[self._dragging_point] = max(0, e.ydata)
            self.target_line.set_ydata(self.target_forces)
            self.canvas.draw_idle()

    def on_release(self, e): self._dragging_point = None

    def run_optimization(self):
        data = self.get_inputs()
        orig_t = np.array([s['t'] for s in data['shims']])
        total_t = np.sum(orig_t)
        
        def obj(ts):
            f = self.model.get_fv_curve(self.velocities, thicknesses=ts)
            return np.mean((f - self.target_forces)**2)
        
        res = minimize(obj, orig_t, method='SLSQP', bounds=[(0.05, 1.0)]*len(orig_t), 
                       constraints={'type': 'eq', 'fun': lambda ts: np.sum(ts) - total_t})
        
        if res.success:
            for i, row in enumerate(self.shim_rows):
                row['t'].delete(0, tk.END); row['t'].insert(0, f"{res.x[i]:.3f}")
            opt_f = self.model.get_fv_curve(self.velocities, thicknesses=res.x)
            self.ax.plot(self.velocities, opt_f, 'g^-', label='Optimized')
            self.ax.legend(); self.canvas.draw()
            messagebox.showinfo("完成", "逆向优化成功，已保持总厚度不变。")

    def load_default_shims(self):
        for s in [(8,22,0.2), (8,20,0.2), (8,18,0.2), (8,16,0.2)]: self.add_shim_row(*s)

if __name__ == "__main__":
    root = tk.Tk()
    DamperApp(root)
    root.mainloop()