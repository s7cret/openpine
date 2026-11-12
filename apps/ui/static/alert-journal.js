"use strict";

const alertJournal = (() => {
  const panel = $("alert-journal");
  const pageSize = 50;
  const kindNames = {alertcondition: "Условие", alert: "Вызов alert()", order_fill: "Исполнение"};
  const pageRequest = new LatestRequest();
  const statusRequest = new LatestRequest();
  const fillRequest = new LatestRequest();
  let replayId = null, liveReplay = null, manual = false, follow = true;
  let page = null, total = 0, pendingPage = null, pendingStatus = null, timer = null;

  function dateText(milliseconds) {
    const date = new Date(milliseconds);
    return Number.isFinite(date.getTime()) ? date.toISOString() : String(milliseconds);
  }

  function status(text) {
    $("alert-journal-status").textContent = text;
  }

  function controls() {
    $("alert-previous").disabled = !page || page.start === 0;
    $("alert-next").disabled = !page || page.next >= total;
    $("alert-latest").disabled = !page || follow;
    $("alert-live-open").disabled = !manual;
    if (page) {
      const range = page.next > page.start ? `${page.start + 1}–${page.next} из ${total}` : "Событий пока нет.";
      status(`${manual ? "Replay" : "Наблюдение"} ${replayId.slice(0, 8)} · ${range}`);
    }
  }

  function hideFill() {
    fillRequest.invalidate();
    $("alert-fill-detail").hidden = true;
    $("alert-fill-values").replaceChildren();
  }

  function openFill(event) {
    const detail = $("alert-fill-detail"), message = $("alert-fill-status"), values = $("alert-fill-values");
    detail.hidden = false;
    detail.focus();
    message.textContent = "Загрузка исполнения…";
    values.replaceChildren();
    const query = new URLSearchParams({bar_index: event.bar_index, fill_id: event.fill_id});
    return fillRequest.run(signal => api(`/api/replays/${replayId}/fill?${query}`, undefined, {signal}), ({event: row, pending}) => {
      if (!row) {
        message.textContent = pending ? "Исполнение ожидает публикации. Повторите после закрытия свечи."
          : "Исполнение отсутствует в опубликованном журнале.";
        return;
      }
      const fill = row.value;
      message.textContent = `Событие ${row.sequence} · ${dateText(fill.observation?.event_time ?? row.time)}`;
      for (const [label, value] of [["Заявка", fill.entry_id], ["Направление", fill.direction === 1 ? "Покупка" : "Продажа"],
          ["Цена", fill.price], ["Количество", fill.quantity], ["Комиссия", fill.commission], ["ID исполнения", fill.fill_id]]) {
        const name = document.createElement("dt"), cell = document.createElement("dd");
        name.textContent = label;
        cell.textContent = value ?? "—";
        values.append(name, cell);
      }
    }).catch(error => { message.textContent = error.message; });
  }

  function renderRows(events) {
    const holder = $("alert-journal-rows");
    const rows = events.map(({event, time}) => {
      const row = document.createElement("tr");
      for (const text of [dateText(time), kindNames[event.kind] ?? event.kind, event.message]) {
        const cell = document.createElement("td");
        cell.textContent = text;
        row.append(cell);
      }
      const action = document.createElement("td");
      if (event.kind === "order_fill") {
        const button = document.createElement("button");
        button.type = "button";
        button.className = "text-button";
        button.textContent = "Показать исполнение";
        button.onclick = () => openFill(event);
        action.append(button);
      }
      row.append(action);
      return row;
    });
    holder.replaceChildren(...rows);
  }

  async function loadPage(start) {
    if (!replayId || !panel.open || pendingPage?.start === start) return;
    const request = {start};
    pendingPage = request;
    if (!page) status("Загрузка оповещений…");
    try {
      await pageRequest.run(signal => api(`/api/replays/${replayId}/alert_events?start=${start}&limit=${pageSize}`,
        undefined, {signal}), result => {
        page = {start, next: result.next, total: result.total};
        total = Math.max(total, result.total);
        renderRows(result.events);
        controls();
      });
    } catch (error) {
      if (pendingPage === request) status(error.message);
    } finally {
      if (pendingPage === request) pendingPage = null;
    }
  }

  function refresh(count) {
    if (!panel.open || !replayId) return;
    total = Math.max(total, count);
    if (pendingPage) return;
    const start = Math.max(0, total - pageSize);
    if (!page || (follow && (page.start !== start || page.total !== total))) return loadPage(start);
    controls();
  }

  function select(id) {
    replayId = id;
    total = 0;
    follow = true;
    page = pendingPage = pendingStatus = null;
    pageRequest.invalidate();
    statusRequest.invalidate();
    hideFill();
    renderRows([]);
    controls();
    status(id ? "Журнал готов к чтению." : "Выберите наблюдение или откройте replay.");
  }

  async function pollReplay() {
    if (!panel.open || !manual || !replayId || pendingStatus) return;
    const request = {};
    pendingStatus = request;
    try {
      await statusRequest.run(signal => api(`/api/replays/${replayId}`, undefined, {signal}),
        state => refresh(state.alerts?.records ?? 0));
    } catch (error) {
      if (pendingStatus === request) status(error.message);
    } finally {
      if (pendingStatus === request) pendingStatus = null;
    }
  }

  function watch() {
    clearInterval(timer);
    if (!panel.open) {
      pageRequest.invalidate();
      statusRequest.invalidate();
      pendingPage = pendingStatus = null;
      hideFill();
    } else if (manual) {
      pollReplay();
      timer = setInterval(pollReplay, 1000);
    } else {
      refresh(liveReplay?.alerts?.records ?? 0);
    }
  }

  function setLive(replay) {
    liveReplay = replay;
    if (manual) return;
    const id = replay?.replay_id ?? null;
    if (id !== replayId) select(id);
    refresh(replay?.alerts?.records ?? 0);
  }

  function useLive() {
    manual = false;
    select(liveReplay?.replay_id ?? null);
    watch();
  }

  panel.addEventListener("toggle", watch);
  $("alert-replay-open").onclick = () => {
    const id = $("alert-replay-id").value.trim();
    if (!/^[0-9a-f]{32}$/.test(id)) { status("Укажите ID replay из 32 шестнадцатеричных символов."); return; }
    manual = true;
    select(id);
    watch();
  };
  $("alert-live-open").onclick = useLive;
  $("alert-previous").onclick = () => { follow = false; loadPage(Math.max(0, page.start - pageSize)); };
  $("alert-next").onclick = () => { follow = false; loadPage(page.next); };
  $("alert-latest").onclick = () => { follow = true; refresh(total); };
  controls();
  return {setLive, useLive};
})();
