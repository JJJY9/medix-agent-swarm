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
    hba1c: ['糖化血红蛋白', 'hba1c', 'a1c', 'glycatedhemoglobin'],
  };

  const LAB_PREFILL_EXCLUDES = {
    creatinine: [
      '尿', 'urine', '清除', 'clearance', '内生', '24小时',
      '比值', 'ratio', '胱抑素', 'cystatin',
    ],
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
    platelets: [
      '平均血小板体积', '血小板平均体积', '血小板压积',
      '血小板分布宽度', '未成熟血小板', '大型血小板', '大血小板',
      '血小板比积', '网织血小板',
      'mpv', 'pdw', 'pct', 'plateletcrit', 'plateletdistribution',
      'plcr',
    ],
    ast: [
      'ast/alt', 'alt/ast', 'astalt', 'altast',
      '谷草/谷丙', '谷丙/谷草', '转氨酶比', 'ratio',
    ],
    alt: [
      'ast/alt', 'alt/ast', 'astalt', 'altast',
      '谷草/谷丙', '谷丙/谷草', '转氨酶比', 'ratio',
    ],
  };

  const UNIT_MG_DL = /mg\/dl/;
  const UNIT_UMOL_L = /[uμµ]mol\/l/;
  const UNIT_G_L = /g\/l/;
  const UNIT_G_DL = /g\/dl/;
  const UNIT_MG_L = /mg\/l/;
  const UNIT_MMOL_L = /mmol\/l/;

  function labUnit(item) {
    return String((item && item.unit) || '')
      .toLowerCase()
      .replace(/\s+/g, '');
  }

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
    const unit = labUnit(item);
    if (UNIT_MG_DL.test(unit)) return { value, converted: false };
    if (UNIT_UMOL_L.test(unit)) {
      return { value: value / 88.4, converted: true };
    }
    // 单位缺失或无法识别时不猜测，避免把 mg/dL 再缩小 88 倍
    return null;
  }

  function labBilirubinMgDl(item) {
    const value = labNumber(item);
    if (value === null) return null;
    const unit = labUnit(item);
    if (UNIT_MG_DL.test(unit)) return { value, converted: false };
    if (UNIT_UMOL_L.test(unit)) {
      return { value: value / 17.1, converted: true };
    }
    return null;
  }

  function labBilirubinUmolL(item) {
    const value = labNumber(item);
    if (value === null) return null;
    const unit = labUnit(item);
    if (UNIT_MG_DL.test(unit)) {
      return { value: value * 17.1, converted: true };
    }
    if (UNIT_UMOL_L.test(unit)) return { value, converted: false };
    return null;
  }

  function labAlbuminGL(item) {
    const value = labNumber(item);
    if (value === null) return null;
    const unit = labUnit(item);
    // 先判断 mg 开头的单位，避免被 g/dL、g/L 子串误匹配
    if (UNIT_MG_DL.test(unit)) {
      return { value: value / 100, converted: true };
    }
    if (UNIT_MG_L.test(unit)) {
      return { value: value / 1000, converted: true };
    }
    if (UNIT_G_DL.test(unit)) {
      return { value: value * 10, converted: true };
    }
    if (UNIT_G_L.test(unit)) return { value, converted: false };
    return null;
  }

  function labUreaMmolL(item) {
    const value = labNumber(item);
    if (value === null) return null;
    const unit = labUnit(item);
    const name = normalizeLabName(item && item.item);
    const isNitrogen = name.includes('尿素氮')
      || name.includes('bun')
      || name.includes('bloodureanitrogen');
    if (UNIT_MG_DL.test(unit)) {
      return {
        value: value / (isNitrogen ? 2.8 : 6.0),
        converted: true,
      };
    }
    if (UNIT_UMOL_L.test(unit)) {
      return { value: value / 1000, converted: true };
    }
    if (UNIT_MMOL_L.test(unit)) return { value, converted: false };
    return null;
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
