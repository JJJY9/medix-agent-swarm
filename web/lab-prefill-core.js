/* 化验预填的纯计算逻辑（不依赖 DOM），供前端与 Node 测试共用。 */
(function (root, factory) {
  const api = factory();
  if (typeof module !== 'undefined' && module.exports) {
    module.exports = api;
  }
  if (root) {
    root.MedixLabPrefill = api;
  }
})(typeof window !== 'undefined' ? window : globalThis, function () {
  const LAB_PREFILL_ALIASES = {
    creatinine: ['肌酐', 'creatinine', 'scr'],
    ast: ['谷草转氨酶', 'ast', 'got'],
    alt: ['谷丙转氨酶', 'alt', 'gpt'],
    platelets: ['血小板', 'plt', 'platelet'],
    bilirubin: ['总胆红素', '胆红素', 'tbil', 'bilirubin'],
    inr: ['国际标准化', 'inr'],
    albumin: ['白蛋白', 'albumin', 'alb'],
    urea: ['尿素氮', '尿素', 'urea', 'bun'],
  };

  const LAB_PREFILL_EXCLUDES = {
    bilirubin: [
      '直接胆红素', '间接胆红素', '结合胆红素', '尿胆红素',
      'direct', 'indirect', 'conjugated', 'urine',
    ],
    albumin: [
      '前白蛋白', '微量白蛋白', '尿白蛋白', '糖化白蛋白',
      '白蛋白排泄率', '球蛋白',
      'prealbumin', 'microalbumin', 'glycated', 'globulin',
    ],
    urea: ['尿酸', 'uric'],
  };

  function normalizeLabName(value) {
    return String(value || '')
      .toLowerCase()
      .replace(/[\s\-_()（）]/g, '');
  }

  function findLabItem(items, key) {
    const aliases = (LAB_PREFILL_ALIASES[key] || []).map(
      normalizeLabName
    );
    const excludes = (LAB_PREFILL_EXCLUDES[key] || []).map(
      normalizeLabName
    );
    return (items || []).find((item) => {
      const name = normalizeLabName(item.item);
      if (!name) return false;
      if (excludes.some((bad) => bad && name.includes(bad))) {
        return false;
      }
      return aliases.some((alias) => alias && name.includes(alias));
    }) || null;
  }

  function labNumber(item) {
    if (!item) return null;
    const raw = item.value;
    if (raw === null || raw === undefined || raw === '') return null;
    const value = Number(raw);
    return Number.isFinite(value) ? value : null;
  }

  function labCreatinineMgDl(item) {
    const value = labNumber(item);
    if (value === null) return null;
    const unit = String((item && item.unit) || '').toLowerCase();
    if (unit.includes('mg')) return { value, converted: false };
    return { value: value / 88.4, converted: true };
  }

  function labBilirubinMgDl(item) {
    const value = labNumber(item);
    if (value === null) return null;
    const unit = String((item && item.unit) || '').toLowerCase();
    if (unit.includes('mg')) return { value, converted: false };
    return { value: value / 17.1, converted: true };
  }

  function labBilirubinUmolL(item) {
    const value = labNumber(item);
    if (value === null) return null;
    const unit = String((item && item.unit) || '').toLowerCase();
    if (unit.includes('mg')) {
      return { value: value * 17.1, converted: true };
    }
    return { value, converted: false };
  }

  function labAlbuminGL(item) {
    const value = labNumber(item);
    if (value === null) return null;
    const unit = String((item && item.unit) || '').toLowerCase();
    if (unit.includes('mg')) {
      return unit.includes('dl')
        ? { value: value / 100, converted: true }
        : { value: value / 1000, converted: true };
    }
    if (unit.includes('dl')) return { value: value * 10, converted: true };
    return { value, converted: false };
  }

  function labUreaMmolL(item) {
    const value = labNumber(item);
    if (value === null) return null;
    const unit = String((item && item.unit) || '').toLowerCase();
    const name = normalizeLabName(item && item.item);
    const isNitrogen = name.includes('尿素氮')
      || name.includes('bun')
      || name.includes('bloodureanitrogen');
    if (unit.includes('mg')) {
      return {
        value: value / (isNitrogen ? 2.8 : 6.0),
        converted: true,
      };
    }
    if (
      unit.includes('μmol')
      || unit.includes('umol')
      || unit.includes('µmol')
    ) {
      return { value: value / 1000, converted: true };
    }
    return { value, converted: false };
  }

  function childPughBilirubinScore(valueUmolL) {
    if (!Number.isFinite(valueUmolL)) return null;
    return valueUmolL < 34 ? 1 : valueUmolL <= 51 ? 2 : 3;
  }

  function childPughAlbuminScore(valueGL) {
    if (!Number.isFinite(valueGL)) return null;
    return valueGL > 35 ? 1 : valueGL >= 28 ? 2 : 3;
  }

  function childPughInrScore(valueInr) {
    if (!Number.isFinite(valueInr)) return null;
    return valueInr < 1.7 ? 1 : valueInr <= 2.3 ? 2 : 3;
  }

  return {
    LAB_PREFILL_ALIASES,
    LAB_PREFILL_EXCLUDES,
    normalizeLabName,
    findLabItem,
    labNumber,
    labCreatinineMgDl,
    labBilirubinMgDl,
    labBilirubinUmolL,
    labAlbuminGL,
    labUreaMmolL,
    childPughBilirubinScore,
    childPughAlbuminScore,
    childPughInrScore,
  };
});
