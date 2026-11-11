"""急救信息卡前端接线测试。"""
from pathlib import Path

WEB_DIR = Path(__file__).resolve().parent.parent / "web"
SCRIPT = (WEB_DIR / "app.js").read_text(encoding="utf-8")
HTML = (WEB_DIR / "index.html").read_text(encoding="utf-8")


def test_emergency_card_entry_exists():
    assert 'id="emergency-card"' in HTML
    assert 'id="emergency-card-status"' in HTML
    assert "急救卡" in HTML
    assert "emergencyCard: document.getElementById('emergency-card')" in SCRIPT
    assert (
        "els.emergencyCard.addEventListener('click', openEmergencyCard)"
        in SCRIPT
    )
    assert "/api/health/emergency-card?profile=" in SCRIPT
    assert "encodeURIComponent(profile)" in SCRIPT
    assert "URL.createObjectURL(blob)" in SCRIPT
    assert "浏览器拦截了新窗口" in SCRIPT
    assert "请先选择患者档案" in SCRIPT


def test_profile_context_has_blood_type_and_contacts():
    assert 'id="ctx-blood-type"' in HTML
    assert 'id="ctx-emergency-1"' in HTML
    assert 'id="ctx-emergency-2"' in HTML
    assert "AB+" in HTML
    assert "context.blood_type" in SCRIPT
    assert "context.emergency_contacts" in SCRIPT
    assert "els.ctxBloodType.value = bloodType" in SCRIPT
    assert "els.ctxBloodType.appendChild(option)" in SCRIPT
    assert "els.ctxEmergency1.value = contacts[0]" in SCRIPT
    assert "els.ctxEmergency2.value = contacts[1]" in SCRIPT
    assert "emergencyContacts.join('；')" in SCRIPT


def test_context_summary_mentions_emergency_fields():
    assert "血型 ${context.blood_type}" in SCRIPT
    assert "紧急联系人 ${contactCount} 位" in SCRIPT
    assert "data.emergency_contacts.split(/[；;]/)" in SCRIPT


def test_contacts_not_sent_to_model_and_exports_include_blood_type():
    assert "delete context.emergency_contacts;" in SCRIPT
    assert "blood_type: '血型'" in SCRIPT
