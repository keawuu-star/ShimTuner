import numpy as np
import tkinter as tk
from tkinter import ttk, messagebox
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
import matplotlib.pyplot as plt
from scipy.optimize import minimize
from scipy.interpolate import interp1d

# ==========================================
# 1. 物理模型核心 (与之前类似，增加动态更新功能)
# ==========================================
class DamperModel:
    def __init__(self, bleed_area, shims):
        self.bleed_area = bleed_area
        self.shims = shims
        self.rho = 850
        self.Cd = 0.65
        self.piston_area = 1000
        self.port_diameter = 10
        
    def calculate_equivalent_stiffness(self, thicknesses=None):
        k_total = 0
        for i, shim in enumerate(self.shims):
            t = thicknesses[i] if thicknesses is not None else shim['t']
            if t <= 0: t = 1e-6 
            d_in, d_out = shim['d_in'], shim['d_out']
            # 简化的单片抗弯刚度贡献
            k_i = (t ** 3) / max((d_out**2 - d_in**2), 1)
            k_total += k_i
        return k_total * 1e7  

    def get_fv_curve(self, velocities, thicknesses=None):
        k_eq = self.calculate_equivalent_stiffness(thicknesses)
        delta_p = np.linspace(0, 20e6, 500) 
        
        A_bleed_m2 = self.bleed_area * 1e-6
        Q_bleed = self.Cd * A_bleed_m2 * np.sqrt(2 * delta_p / self.rho)
        
        A_port_m2 = (np.pi * (self.port_diameter * 1e-3)**2) / 4
        deflection = (delta_p * A_port_m2) / k_eq
        
        A_shim_m2 = np.pi * (self.port_diameter * 1e-3) * deflection
        Q_shim = self.Cd * A_shim_m2 * np.sqrt(2 * delta_p / self.rho)
        
        Q_total = Q_bleed + Q_shim
        v_calc = Q_total / (self.piston_area * 1e-6)
        f_calc = delta_p * (self.piston_area * 1e-6)
        
        v_calc, unique_indices = np.unique(v_calc, return_index=True)
        f_calc = f_calc[unique_indices]
        
        interp_func = interp1d(v_calc, f_calc, kind='linear', fill_value="extrapolate")
        return interp_func(velocities)

# ==========================================
# 2. Tkinter GUI 界面与交互逻辑
# ==========================================
class DamperApp:
    def __init__(self, root):
        self.root = root
        self.root.title("Damper Shim Stack Simulator & Optimizer")
        self.root.geometry("1200x700")
        
        # 初始数据
        self.velocities = np.arange(0, 10.5, 0.5)
        self.model = DamperModel(bleed_area=2.5, shims=[])
        
        # GUI 组件列表
        self.shim_rows = [] 
        
        self.setup_ui()
        self.load_default_shims()
        self.simulate_current() # 初始化绘图
        
    def setup_ui(self):
        # 左侧控制面板
        self.panel_left = ttk.Frame(self.root, width=350, padding=10)
        self.panel_left.pack(side=tk.LEFT, fill=tk.Y)
        
        # 溢流口设置
        frame_bleed = ttk.LabelFrame(self.panel_left, text="Bleed Port (溢流口)", padding=5)
        frame_bleed.pack(fill=tk.X, pady=5)
        ttk.Label(frame_bleed, text="Area (mm²):").pack(side=tk.LEFT)
        self.entry_bleed = ttk.Entry(frame_bleed, width=10)
        self.entry_bleed.insert(0, "2.5")
        self.entry_bleed.pack(side=tk.LEFT, padx=5)
        
        # 阀片列表容器
        self.frame_shims_container = ttk.LabelFrame(self.panel_left, text="Shim Stack (阀片堆叠, 从活塞侧起)", padding=5)
        self.frame_shims_container.pack(fill=tk.BOTH, expand=True, pady=5)
        
        # 表头
        header = ttk.Frame(self.frame_shims_container)
        header.pack(fill=tk.X, pady=2)
        ttk.Label(header, text="d_in(mm)", width=10).pack(side=tk.LEFT)
        ttk.Label(header, text="d_out(mm)", width=10).pack(side=tk.LEFT)
        ttk.Label(header, text="t(mm)", width=10).pack(side=tk.LEFT)
        
        # 阀片行存放区
        self.frame_shims = ttk.Frame(self.frame_shims_container)
        self.frame_shims.pack(fill=tk.BOTH, expand=True)
        
        # 操作按钮
        btn_frame = ttk.Frame(self.panel_left)
        btn_frame.pack(fill=tk.X, pady=10)
        ttk.Button(btn_frame, text="+ Add Shim (添加)", command=self.add_shim_row).pack(fill=tk.X, pady=2)
        ttk.Button(btn_frame, text="▶ Simulate Current (模拟当前堆叠)", command=self.simulate_current).pack(fill=tk.X, pady=2)
        ttk.Button(btn_frame, text="★ Run Reverse Optimization (逆向优化)", command=self.run_optimization).pack(fill=tk.X, pady=10)
        
        # 右侧绘图区
        self.panel_right = ttk.Frame(self.root)
        self.panel_right.pack(side=tk.RIGHT, fill=tk.BOTH, expand=True)
        
        self.fig, self.ax = plt.subplots(figsize=(8, 6))
        self.canvas = FigureCanvasTkAgg(self.fig, master=self.panel_right)
        self.canvas.get_tk_widget().pack(fill=tk.BOTH, expand=True)
        
        # 拖拽交互状态
        self._dragging_point = None
        self.target_forces = np.zeros_like(self.velocities)
        self.canvas.mpl_connect('button_press_event', self.on_press)
        self.canvas.mpl_connect('motion_notify_event', self.on_motion)
        self.canvas.mpl_connect('button_release_event', self.on_release)

    def load_default_shims(self):
        defaults = [
            (8, 22, 0.20), (8, 20, 0.20), (8, 18, 0.25), (8, 16, 0.25), (8, 14, 0.30)
        ]
        for din, dout, t in defaults:
            self.add_shim_row(din, dout, t)

    def add_shim_row(self, din=8, dout=20, t=0.2):
        row_frame = ttk.Frame(self.frame_shims)
        row_frame.pack(fill=tk.X, pady=2)
        
        e_din = ttk.Entry(row_frame, width=8)
        e_din.insert(0, str(din))
        e_din.pack(side=tk.LEFT, padx=2)
        
        e_dout = ttk.Entry(row_frame, width=8)
        e_dout.insert(0, str(dout))
        e_dout.pack(side=tk.LEFT, padx=2)
        
        e_t = ttk.Entry(row_frame, width=8)
        e_t.insert(0, str(t))
        e_t.pack(side=tk.LEFT, padx=2)
        
        btn_del = ttk.Button(row_frame, text="X", width=3, command=lambda f=row_frame: self.delete_shim_row(f))
        btn_del.pack(side=tk.LEFT, padx=5)
        
        self.shim_rows.append({
            'frame': row_frame,
            'd_in': e_din,
            'd_out': e_dout,
            't': e_t
        })

    def delete_shim_row(self, frame_to_delete):
        for row in self.shim_rows:
            if row['frame'] == frame_to_delete:
                row['frame'].destroy()
                self.shim_rows.remove(row)
                break

    def read_gui_inputs(self):
        try:
            bleed = float(self.entry_bleed.get())
            shims = []
            for row in self.shim_rows:
                din = float(row['d_in'].get())
                dout = float(row['d_out'].get())
                t = float(row['t'].get())
                if dout <= din or t <= 0:
                    raise ValueError("Invalid shim dimensions (d_out must be > d_in, t must be > 0)")
                shims.append({'d_in': din, 'd_out': dout, 't': t})
            return bleed, shims
        except ValueError as e:
            messagebox.showerror("Input Error", f"Please enter valid numbers.\n{str(e)}")
            return None, None

    def simulate_current(self):
        bleed, shims = self.read_gui_inputs()
        if shims is None: return
        
        # 更新模型
        self.model.bleed_area = bleed
        self.model.shims = shims
        
        # 计算当前阻力
        self.current_forces = self.model.get_fv_curve(self.velocities)
        
        # 如果是第一次模拟，或者拖拽目标线与当前线差距过大，将拖拽目标线重置为当前模拟线
        self.target_forces = self.current_forces.copy()
        
        self.update_plot()

    def update_plot(self):
        self.ax.clear()
        self.ax.set_title("F-v Curve: Drag Red Points to Set Target", fontsize=12)
        self.ax.set_xlabel("Velocity (m/s)")
        self.ax.set_ylabel("Damping Force (N)")
        self.ax.grid(True)
        self.ax.set_xlim(0, 10.5)
        self.ax.set_ylim(0, max(self.current_forces) * 2.0 + 100)
        
        # 绘制当前模拟线
        self.ax.plot(self.velocities, self.current_forces, 'b--', label='Simulated Stack')
        
        # 绘制可拖动目标线
        self.target_line, = self.ax.plot(self.velocities, self.target_forces, 'ro-', markersize=8, label='Target Curve (Draggable)')
        
        self.ax.legend()
        self.canvas.draw()

    # --- 拖拽交互逻辑 ---
    def on_press(self, event):
        if event.inaxes != self.ax: return
        distances = np.sqrt(((self.velocities - event.xdata) / 10.5)**2 + 
                            ((self.target_forces - event.ydata) / self.ax.get_ylim()[1])**2)
        closest_idx = np.argmin(distances)
        if distances[closest_idx] < 0.05:
            self._dragging_point = closest_idx

    def on_motion(self, event):
        if self._dragging_point is None or event.inaxes != self.ax: return
        new_force = max(0, event.ydata)
        self.target_forces[self._dragging_point] = new_force
        self.target_line.set_ydata(self.target_forces)
        self.canvas.draw_idle()

    def on_release(self, event):
        self._dragging_point = None

    # --- 逆向优化逻辑 ---
    def run_optimization(self):
        bleed, shims = self.read_gui_inputs()
        if shims is None or len(shims) == 0: return
        
        orig_thicknesses = np.array([s['t'] for s in shims])
        target_total_thickness = np.sum(orig_thicknesses)
        
        def objective(t_array):
            simulated_forces = self.model.get_fv_curve(self.velocities, thicknesses=t_array)
            return np.mean((simulated_forces - self.target_forces)**2)
        
        # 约束：总厚度等于当前输入的总厚度
        constraints = {'type': 'eq', 'fun': lambda t: np.sum(t) - target_total_thickness}
        bounds = [(0.05, 1.0) for _ in orig_thicknesses]
        
        result = minimize(objective, orig_thicknesses, method='SLSQP', bounds=bounds, constraints=constraints)
        
        if result.success:
            # 1. 更新GUI输入框中的厚度数据
            for i, row in enumerate(self.shim_rows):
                row['t'].delete(0, tk.END)
                row['t'].insert(0, f"{result.x[i]:.3f}")
            
            # 2. 绘制优化结果对比
            opt_forces = self.model.get_fv_curve(self.velocities, thicknesses=result.x)
            self.ax.plot(self.velocities, opt_forces, 'g^-', label='Optimized Output')
            self.ax.legend()
            self.canvas.draw()
            
            messagebox.showinfo("Success", f"Optimization Successful!\nTotal Thickness Maintained: {target_total_thickness:.3f} mm")
        else:
            messagebox.showwarning("Warning", "Optimization Failed or did not converge perfectly.")

if __name__ == "__main__":
    root = tk.Tk()
    app = DamperApp(root)
    root.mainloop()