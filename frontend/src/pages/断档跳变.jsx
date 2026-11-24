import React, { useCallback, useEffect, useState } from "react";
import { api } from "../api.js";

function 布尔徽章({ 值, 真文 = "是", 假文 = "否" }) {
  return <span className={`徽章 ${值 ? "通" : "败"}`}>{值 ? 真文 : 假文}</span>;
}

export default function 断档跳变() {
  const [断档, set断档] = useState([]);
  const [跳变, set跳变] = useState([]);
  const [桥接, set桥接] = useState([]);
  const [户, set户] = useState("户甲");
  const [日, set日] = useState("2026-11-22");
  const [处置, set处置] = useState(null);
  const [讯, set讯] = useState("");
  const [忙, set忙] = useState(false);

  const 刷新底表 = useCallback(async () => {
    set断档(await api.断档());
    set跳变(await api.跳变());
    set桥接(await api.断档桥接());
  }, []);

  const 查询处置 = useCallback(async () => {
    if (!户.trim() || !日.trim()) {
      set讯("请填写账户键与营业日");
      return;
    }
    try {
      const d = await api.断档处置(`?账户键=${encodeURIComponent(户.trim())}&营业日=${encodeURIComponent(日.trim())}`);
      set处置(d);
      set讯("");
    } catch (e) {
      set处置(null);
      set讯(String(e.message || e));
    }
  }, [户, 日]);

  useEffect(() => {
    刷新底表().catch((e) => set讯(String(e.message || e)));
  }, [刷新底表]);

  const 动作 = async (fn, 成功前缀) => {
    set忙(true);
    set讯("");
    try {
      const r = await fn();
      set讯(`${成功前缀}：${JSON.stringify(r)}`);
      await 查询处置();
      await 刷新底表();
    } catch (e) {
      set讯(String(e.message || e));
    } finally {
      set忙(false);
    }
  };

  const 桥接中 = 处置?.处置 === "桥接阻断";

  return (
    <div>
      <h1 className="题">断档跳变</h1>
      <p className="述">
        全局口径只贯彻一种：<b>同票补齐</b>——非开户首日缺前日镜时，抽取事务内先物化缺失日镜再写当日，
        当日有效镜存在且开端来自补齐日，绝不跨断档以零开端落正式有效镜。显式「桥接阻断」是旁置处置：
        桥接存时当日整拒、无有效镜，解除后重抽两端回放守恒；桥接与当日有效镜事务级互斥。
      </p>

      <div className="段">
        <h2 className="段题">断档处置只读接口（缺日 · 处置 · 当日可否抽 · valid）</h2>
        <div className="行">
          <input value={户} onChange={(e) => set户(e.target.value)} placeholder="账户键" style={{ maxWidth: 160 }} />
          <input value={日} onChange={(e) => set日(e.target.value)} placeholder="YYYY-MM-DD" style={{ maxWidth: 180 }} />
          <button onClick={() => 查询处置().catch((e) => set讯(String(e.message || e)))}>查询处置</button>
          <button
            className="次"
            onClick={() => 刷新底表().then(() => 查询处置()).catch((e) => set讯(String(e.message || e)))}
          >
            刷新全部
          </button>
        </div>

        {处置 ? (
          <table>
            <tbody>
              <tr><th style={{ width: 150 }}>账户 × 营业日</th><td>{处置.账户键} · {处置.营业日}（开户日 {处置.开户营业日}）</td></tr>
              <tr><th>缺日</th><td>{处置.缺日?.length ? 处置.缺日.join("，") : "无（前日链连续或开户首日）"}</td></tr>
              <tr><th>处置</th><td><b>{处置.处置}</b> — {处置.处置说明}</td></tr>
              <tr><th>当日可否抽</th><td><布尔徽章 值={处置.当日可否抽} 真文="可抽" 假文="禁抽" /></td></tr>
              <tr><th>valid</th><td><布尔徽章 值={处置.valid} 真文="valid" 假文="invalid" /></td></tr>
              <tr><th>当日有效镜</th><td><布尔徽章 值={处置.当日有效镜} 真文="存在" 假文="不存在" />{处置.当日余额 != null ? `　余额 ${处置.当日余额}；开端来源 ${处置.开端来源 || "—"}` : ""}</td></tr>
              <tr><th>桥接凭证</th><td>{处置.桥接凭证号 || "无"}{处置.桥接横断链 ? `（桥接横断补齐链，阻断日 ${处置.阻断桥接营业日}）` : ""}</td></tr>
            </tbody>
          </table>
        ) : <div className="空态">输入账户键与营业日后查询处置状态</div>}

        <div className="行" style={{ marginTop: "0.8rem" }}>
          <button
            className="水"
            disabled={忙 || !处置 || !处置.当日可否抽}
            title={!处置?.当日可否抽 ? "桥接阻断中，须先解除" : "同一事务内补齐缺失日并抽取当日"}
            onClick={() => 动作(() => api.同票补齐({ 账户键: 户.trim(), 营业日: 日.trim() }), "同票补齐/抽取完成")}
          >
            同票补齐并抽取
          </button>
          {!桥接中 ? (
            <button
              className="次"
              disabled={忙 || !处置}
              onClick={() => 动作(() => api.立桥接({ 账户键: 户.trim(), 营业日: 日.trim(), 原因: "断档页人工立桥" }), "桥接凭证已立")}
            >
              立桥接阻断（当日整拒）
            </button>
          ) : (
            <button
              disabled={忙}
              onClick={() => 动作(() => api.解桥接({
              凭证号: 处置.桥接凭证号 || "",
              账户键: 户.trim(),
              营业日: 处置.阻断桥接营业日 || 日.trim(),
            }), "桥接已解除，可重抽两端")}
            >
              解除桥接
            </button>
          )}
        </div>
      </div>

      {讯 ? <p className="讯">{讯}</p> : null}

      <div className="段">
        <h2 className="段题">断档桥接凭证</h2>
        {!桥接.length ? <div className="空态">无桥接凭证</div> : (
          <table>
            <thead><tr><th>凭证号</th><th>账户键</th><th>目标日</th><th>缺日</th><th>状态</th><th>原因</th><th>时刻</th></tr></thead>
            <tbody>
              {桥接.map((b) => (
                <tr key={b.凭证号}>
                  <td>{b.凭证号}</td>
                  <td>{b.账户键}</td>
                  <td>{b.目标营业日}</td>
                  <td>{(b.缺日 || []).join("，")}</td>
                  <td><span className={`徽章 ${b.状态 === "有效" ? "败" : "通"}`}>{b.状态}</span></td>
                  <td>{b.原因}</td>
                  <td>{b.时刻}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>

      <div className="段">
        <h2 className="段题">断档记录</h2>
        {!断档.length ? <div className="空态">无断档</div> : (
          <table>
            <thead><tr><th>账户键</th><th>缺营业日</th><th>处置</th><th>批次</th><th>时刻</th></tr></thead>
            <tbody>
              {断档.map((r) => (
                <tr key={r.编号}>
                  <td>{r.账户键}</td><td>{r.缺营业日}</td><td>{r.处置}</td><td>{r.检出批次编号}</td><td>{r.时刻}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>

      <div className="段">
        <h2 className="段题">跳变记录</h2>
        {!跳变.length ? <div className="空态">无跳变</div> : (
          <table>
            <thead><tr><th>账户键</th><th>营业日</th><th>前日</th><th>当日</th><th>流水净额</th><th>偏离</th></tr></thead>
            <tbody>
              {跳变.map((r) => (
                <tr key={r.编号}>
                  <td>{r.账户键}</td><td>{r.营业日}</td><td>{r.前日余额}</td><td>{r.当日余额}</td><td>{r.流水净额}</td><td>{r.偏离}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </div>
  );
}
