"""Centralized Win11 visual language for the Main.vi replica.

The production monitor is a 1920x1080 display at 100% scaling.  Keeping the
measurements and palette here makes it possible to validate the layout without
chasing pixel values through every page builder.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class UiMetrics:
    """Canonical geometry and type scale (logical pixels at 100% scaling)."""

    canonical_width: int = 1920
    canonical_height: int = 1080
    page_margin: int = 16
    nav_height: int = 44
    top_controls_height: int = 120
    scanner_height: int = 40
    footer_max_height: int = 120
    station_gap: int = 12
    card_radius: int = 10
    control_radius: int = 6
    page_title: int = 18
    operation_text: int = 15
    label_text: int = 13
    input_height: int = 36
    primary_button_height: int = 40
    table_row_height: int = 22
    table_header_height: int = 34

    # Descriptive aliases keep geometry assertions readable and provide a
    # stable public vocabulary for downstream screenshot tooling.
    @property
    def title_font_size(self) -> int:
        return self.page_title

    @property
    def operation_font_size(self) -> int:
        return self.operation_text

    @property
    def label_font_size(self) -> int:
        return self.label_text

    @property
    def input_height_px(self) -> int:
        return self.input_height

    @property
    def primary_button_height_px(self) -> int:
        return self.primary_button_height

    @property
    def table_row_height_px(self) -> int:
        return self.table_row_height

    @property
    def card_radius_px(self) -> int:
        return self.card_radius

    @property
    def control_radius_px(self) -> int:
        return self.control_radius


@dataclass(frozen=True)
class UiPalette:
    """Light Win11 palette with semantic status colors."""

    page: str = "#f5f7fb"
    surface: str = "#ffffff"
    surface_alt: str = "#f8fafc"
    border: str = "#d9e1ec"
    border_strong: str = "#c6d1df"
    text: str = "#172033"
    muted: str = "#5d6b80"
    accent: str = "#2563eb"
    accent_hover: str = "#1d4ed8"
    accent_soft: str = "#e8f0ff"
    ok: str = "#14804a"
    warn: str = "#b76e00"
    ng: str = "#c7373f"
    info: str = "#2f6f9f"
    row_alt: str = "#f5f8fc"
    disabled: str = "#eef2f6"


class UiTextCatalog:
    """Small catalog for labels that must switch as one selected language."""

    LANGUAGES = ("中文", "English", "Français")
    TABS = {
        "中文": ("测试", "设置", "查询", "手动"),
        "English": ("Main", "Setup", "Query", "Manual"),
        "Français": ("Principale", "Coup Monté", "Requête", "Manuelle"),
    }
    ACTIONS = {
        "中文": {"back": "后退", "forward": "前进", "enable": "使能", "disable": "禁用", "automatic": "自动", "manual": "手动", "readback": "PLC 回读"},
        "English": {"back": "Back", "forward": "Forward", "enable": "Enable", "disable": "Disable", "automatic": "Automatic", "manual": "Manual", "readback": "PLC readback"},
        "Français": {"back": "Arrière", "forward": "Avant", "enable": "Activer", "disable": "Désactiver", "automatic": "Automatique", "manual": "Manuel", "readback": "Retour PLC"},
    }
    MESSAGES = {
        "cancelled": {"中文": "已取消", "English": "Cancelled", "Français": "Annulé"},
        "denied": {"中文": "拒绝", "English": "Denied", "Français": "Refusé"},
        "scan_error": {"中文": "扫码错误：{error}", "English": "Scanner error: {error}", "Français": "Erreur scanner : {error}"},
        "first_error": {"中文": "一测错误：{error}", "English": "Test 1 error: {error}", "Français": "Erreur test 1 : {error}"},
        "second_error": {"中文": "二测错误：{error}", "English": "Test 2 error: {error}", "Français": "Erreur test 2 : {error}"},
        "label_error": {"中文": "贴标错误：{error}", "English": "Label error: {error}", "Français": "Erreur étiquette : {error}"},
        "reprint_denied": {"中文": "重打拒绝：{error}", "English": "Reprint denied: {error}", "Français": "Réimpression refusée : {error}"},
        "reset_error": {"中文": "复位错误：{error}", "English": "Reset error: {error}", "Français": "Erreur reset : {error}"},
        "query_range": {"中文": "开始时间不能晚于结束时间", "English": "Start must be before finish", "Français": "Le début doit précéder la fin"},
    }

    # Runtime-visible labels are resolved from this catalog.  Industry
    # identifiers (PLC, ATEQ, QR, COM, OK/NG) deliberately remain unchanged.
    TEXT = {
        "Leak Test 2 Channels / 气密检测": {"中文": "气密检测", "English": "Leak Test 2 Channels", "Français": "Test d'étanchéité à deux voies"},
        "参数设置 / Setup / Coup monté": {"中文": "参数设置", "English": "Setup", "Français": "Configuration"},
        "Language": {"中文": "语言", "English": "Language", "Français": "Langue"},
        "● Scanner": {"中文": "● 扫码器", "English": "● Scanner", "Français": "● Scanner"},
        "Scanner": {"中文": "扫码器", "English": "Scanner", "Français": "Scanner"},
        "保存设置 / Save Parameters": {"中文": "保存设置", "English": "Save Parameters", "Français": "Enregistrer les paramètres"},
        "Calibration Period (Hours)": {"中文": "校准周期（小时）", "English": "Calibration Period (Hours)", "Français": "Période de calibration (heures)"},
        "校准倒计时 / Calibration Countdown": {"中文": "校准倒计时", "English": "Calibration countdown", "Français": "Compte à rebours calibration"},
        "已提交：SIM-PART": {"中文": "已提交：SIM-PART", "English": "Committed: SIM-PART", "Français": "Validé : SIM-PART"},
        "等待 NG 样件": {"中文": "等待 NG 样件", "English": "Waiting for NG sample", "Français": "En attente de l'échantillon NG"},
        "登录 / Login / Connexion": {"中文": "登录", "English": "Login", "Français": "Connexion"},
        "未登录 / operator": {"中文": "未登录", "English": "Not signed in", "Français": "Non connecté"},
        "登录 Role": {"中文": "登录角色", "English": "Role", "Français": "Rôle"},
        "密码 Password": {"中文": "密码", "English": "Password", "Français": "Mot de passe"},
        "确定 / Confirm": {"中文": "确定", "English": "Confirm", "Français": "Confirmer"},
        "退出 / Exit": {"中文": "退出", "English": "Exit", "Français": "Quitter"},
        "Scanner frame / 扫码帧": {"中文": "扫码帧", "English": "Scanner frame", "Français": "Trame scanner"},
        "Scanner ready / 扫码器就绪": {"中文": "扫码器就绪", "English": "Scanner ready", "Français": "Scanner prêt"},
        "看门狗 / Watchdog": {"中文": "看门狗", "English": "Watchdog", "Français": "Chien de garde"},
        "标签重打 / Reprint": {"中文": "标签重打", "English": "Reprint label", "Français": "Réimprimer"},
        "显示扩展 / Extensions": {"中文": "显示扩展", "English": "Show extensions", "Français": "Afficher les extensions"},
        "管理员恢复 / Recovery": {"中文": "管理员恢复", "English": "Admin recovery", "Français": "Récupération admin"},
        "处理原因 / reason": {"中文": "处理原因", "English": "Reason", "Français": "Motif"},
        "无恢复操作": {"中文": "无恢复操作", "English": "No recovery action", "Français": "Aucune action de récupération"},
        "Part No. draft": {"中文": "产品型号", "English": "Part No.", "Français": "N° pièce"},
        "Part No. / 产品型号": {"中文": "产品型号", "English": "Part No.", "Français": "N° pièce"},
        "OK": {"中文": "确定", "English": "OK", "Français": "OK"},
        "Runtime extension": {"中文": "运行状态扩展", "English": "Runtime extension", "Français": "Extension d'état"},
        "SIMULATE readback / 快捷诊断": {"中文": "SIMULATE 回读", "English": "SIMULATE readback", "Français": "Retour SIMULATE"},
        "已认证": {"中文": "已认证", "English": "Authenticated", "Français": "Authentifié"},
        "登录失败": {"中文": "登录失败", "English": "Sign-in failed", "Français": "Échec de connexion"},
    }

    @classmethod
    def tabs(cls, language: str) -> tuple[str, ...]:
        return cls.TABS.get(language, cls.TABS["中文"])

    @classmethod
    def action(cls, language: str, key: str) -> str:
        return cls.ACTIONS.get(language, cls.ACTIONS["中文"]).get(key, key)

    @classmethod
    def message(cls, language: str, key: str, **values: object) -> str:
        template = cls.MESSAGES.get(key, {}).get(language, cls.MESSAGES.get(key, {}).get("中文", key))
        # UI diagnostics intentionally omit raw service exception text.  The
        # service/audit log retains the original exception; visible text stays
        # within the selected locale.
        values.setdefault("error", {"中文": "操作失败", "English": "Operation failed", "Français": "Opération échouée"}.get(language, "Operation failed"))
        return template.format(**values)

    @classmethod
    def translate(cls, text: str, language: str) -> str:
        """Translate one visible source label without translating user data."""
        if text in cls.TEXT:
            return cls.TEXT[text].get(language, cls.TEXT[text]["中文"])
        # Common station-specific labels.
        for station in ("A", "B"):
            if text == f"工位 {station} / Station {station}":
                return {"中文": f"工位 {station}", "English": f"Station {station}", "Français": f"Poste {station}"}[language]
            if text == f"{station} List":
                return {"中文": f"{station} 列表", "English": f"{station} List", "Français": f"Liste {station}"}[language]
            if text == f"{station} List Search":
                return {"中文": f"{station} 列表查询", "English": f"{station} List Search", "Français": f"Recherche liste {station}"}[language]
            if text == f"归档工位 {station}":
                return {"中文": f"归档工位 {station}", "English": f"Archive station {station}", "Français": f"Archiver le poste {station}"}[language]
        pairs = {
            "Single/Dual": {"中文": "单/双测", "English": "Single/Dual", "Français": "Simple/Double"},
            "2D Code": {"中文": "二维码", "English": "2D Code", "Français": "Code 2D"},
            "Total Today": {"中文": "今日总数", "English": "Total Today", "Français": "Total du jour"},
            "OK Today": {"中文": "今日 OK", "English": "OK Today", "Français": "OK du jour"},
            "ATEQ No.": {"中文": "ATEQ 编号", "English": "ATEQ No.", "Français": "N° ATEQ"},
            "Part No.": {"中文": "产品型号", "English": "Part No.", "Français": "N° pièce"},
            "Staff": {"中文": "人员", "English": "Staff", "Français": "Personnel"},
            "Start Time": {"中文": "开始时间", "English": "Start Time", "Français": "Début"},
            "Finish Time": {"中文": "结束时间", "English": "Finish Time", "Français": "Fin"},
            "Result": {"中文": "结果", "English": "Result", "Français": "Résultat"},
            "Search": {"中文": "查询", "English": "Search", "Français": "Rechercher"},
            "Download": {"中文": "下载", "English": "Download", "Français": "Télécharger"},
        }
        if text.startswith("ATEQ F620 "):
            station = text.split()[2]
            return {"中文": f"ATEQ F620 {station}（重启软件后生效）", "English": f"ATEQ F620 {station} (restart software to activate)", "Français": f"ATEQ F620 {station} (redémarrer pour activer)"}[language]
        if text.startswith("校准倒计时"):
            return {"中文": "校准倒计时", "English": "Calibration countdown", "Français": "Compte à rebours calibration"}[language]
        if text.startswith("A Cal.") or text.startswith("B Cal."):
            station = text[0]
            return {"中文": f"{station} 校准到期", "English": f"{station} Cal. Time", "Français": f"{station} Temps cal."}[language]
        for token, values in pairs.items():
            if text.startswith(token):
                suffix = text[len(token):].strip()
                return values[language] + (f" {suffix}" if suffix else "")
        return text


METRICS = UiMetrics()
PALETTE = UiPalette()


def stylesheet() -> str:
    """Return the one application-wide stylesheet."""

    m, p = METRICS, PALETTE
    return f"""
    * {{ color: {p.text}; font-family: 'Segoe UI', 'Microsoft YaHei UI', sans-serif; font-size: {m.label_text}px; }}
    QMainWindow, QWidget#page {{ background: {p.page}; }}
    QTabWidget::pane {{ border: 0; background: {p.page}; top: -1px; }}
    QTabBar {{ qproperty-expanding: false; background: transparent; }}
    QTabBar::tab {{ background: transparent; border: 0; border-bottom: 2px solid transparent; min-height: {m.nav_height - 2}px; padding: 0 20px; color: {p.muted}; font-size: {m.operation_text}px; }}
    QTabBar::tab:selected {{ color: {p.accent}; border-bottom: 2px solid {p.accent}; font-weight: 600; }}
    QTabBar::tab:hover {{ color: {p.accent_hover}; background: {p.accent_soft}; }}
    QFrame#stationCard_A, QFrame#stationCard_B {{ background: {p.surface}; border: 1px solid {p.border}; border-radius: {m.card_radius}px; }}
    QFrame#settingsCard {{ background: {p.surface}; border: 1px solid {p.border}; border-radius: {m.card_radius}px; }}
    QGroupBox {{ background: {p.surface}; border: 1px solid {p.border}; border-radius: {m.card_radius}px; margin-top: 10px; padding-top: 14px; font-size: {m.operation_text}px; font-weight: 600; }}
    QGroupBox#bottomIndicators_A, QGroupBox#bottomIndicators_B {{ margin-top: 0px; padding-top: 0px; }}
    QGroupBox::title {{ subcontrol-origin: margin; left: 14px; padding: 0 6px; color: {p.text}; }}
    QLabel#pageTitle {{ font-size: {m.page_title}px; font-weight: 600; }}
    QLabel#resultBanner {{ min-height: 70px; padding: 14px; background: {p.accent_soft}; border: 1px solid {p.border}; border-radius: {m.card_radius}px; font-size: 24px; }}
    QLabel#stationTitle, QGroupBox[stationTitle="true"] {{ font-size: {m.page_title}px; font-weight: 600; }}
    QLabel#statusOk, QLabel[state="ok"] {{ color: {p.ok}; }}
    QLabel#statusWarn, QLabel[state="warn"] {{ color: {p.warn}; }}
    QLabel#statusNg, QLabel[state="ng"] {{ color: {p.ng}; }}
    QLabel#statusInfo, QLabel[state="info"] {{ color: {p.info}; }}
    QLineEdit, QComboBox, QSpinBox, QTimeEdit, QDateTimeEdit {{ min-height: {m.input_height}px; border: 1px solid {p.border_strong}; border-radius: {m.control_radius}px; background: {p.surface}; padding: 0 10px; selection-background-color: {p.accent}; }}
    QSpinBox[compact="true"] {{ min-height: 24px; max-height: 30px; padding: 0 6px; }}
    QLineEdit:focus, QComboBox:focus, QSpinBox:focus, QTimeEdit:focus, QDateTimeEdit:focus {{ border: 2px solid {p.accent}; padding: 0 9px; }}
    QPushButton {{ min-height: {m.primary_button_height}px; border: 1px solid {p.border_strong}; border-radius: {m.control_radius}px; background: {p.surface}; padding: 0 14px; font-size: {m.operation_text}px; }}
    QPushButton[compact="true"] {{ min-height: 24px; max-height: 32px; padding: 0 10px; font-size: {m.label_text}px; }}
    QPushButton:hover {{ border-color: {p.accent}; background: {p.accent_soft}; }}
    QPushButton:pressed, QPushButton:checked {{ background: {p.accent}; color: white; border-color: {p.accent}; }}
    QPushButton:disabled {{ color: {p.muted}; background: {p.disabled}; border-color: {p.border}; }}
    QPushButton[primary="true"] {{ background: {p.accent}; color: white; border-color: {p.accent}; font-weight: 600; }}
    QPushButton[destructive="true"] {{ color: {p.ng}; }}
    QTableWidget {{ background: {p.surface}; alternate-background-color: {p.row_alt}; border: 1px solid {p.border}; border-radius: {m.control_radius}px; gridline-color: {p.border}; font-size: {m.label_text}px; }}
    QTableWidget::item {{ padding: 0 8px; }}
    QHeaderView::section {{ background: {p.surface_alt}; color: {p.muted}; border: 0; border-bottom: 1px solid {p.border}; padding: 0 8px; font-size: {m.label_text}px; font-weight: 600; }}
    QScrollBar:vertical {{ width: 10px; background: transparent; }}
    QScrollBar::handle:vertical {{ background: {p.border_strong}; border-radius: 5px; min-height: 24px; }}
    QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
    """


__all__ = ["UiMetrics", "UiPalette", "UiTextCatalog", "METRICS", "PALETTE", "stylesheet"]
