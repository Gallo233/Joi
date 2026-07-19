(function () {
  const scriptUrl = document.currentScript?.src || new URL("joi-floating-assistant.js", window.location.href).href;
  const defaultAssetBase = new URL("./assets/", scriptUrl).href;

  const clamp = (value, min, max) => Math.min(max, Math.max(min, value));
  const now = () => performance.now();

  class JoiFloatingAssistant extends HTMLElement {
    constructor() {
      super();
      this.attachShadow({ mode: "open" });
      this.state = {
        connected: false,
        dragging: false,
        expanded: false,
        pointerX: 0,
        pointerY: 0,
        x: 0,
        y: 0,
        width: 210,
        height: 520,
        offsetX: 0,
        offsetY: 0,
        lastDragX: 0,
        lastDragY: 0,
        lastMoveAt: 0,
        velocityX: 0,
        velocityY: 0,
        positioned: false,
      };
      this.socket = null;
      this.nextId = 1;
      this.messages = [];
      this.reconnectTimer = 0;
      this.handleWindowPointerMove = this.handleWindowPointerMove.bind(this);
      this.handleResize = this.handleResize.bind(this);
    }

    static get observedAttributes() {
      return ["core-url", "asset-base", "start-open"];
    }

    connectedCallback() {
      this.render();
      this.cacheParts();
      this.setInitialPosition();
      this.bindEvents();
      this.updatePosition();
      this.setMood("idle");
      this.connect();
    }

    disconnectedCallback() {
      window.removeEventListener("pointermove", this.handleWindowPointerMove);
      window.removeEventListener("resize", this.handleResize);
      window.clearTimeout(this.reconnectTimer);
      this.socket?.close();
    }

    attributeChangedCallback() {
      if (!this.isConnected) return;
      if (this.shadowRoot) {
        this.render();
        this.cacheParts();
        this.bindEvents();
        this.updatePosition();
      }
    }

    get assetBase() {
      return this.getAttribute("asset-base") || defaultAssetBase;
    }

    get coreUrl() {
      return this.getAttribute("core-url") || "ws://127.0.0.1:8765";
    }

    render() {
      const openClass = this.state.expanded || this.hasAttribute("start-open") ? " is-open" : "";
      this.state.expanded = this.state.expanded || this.hasAttribute("start-open");
      this.shadowRoot.innerHTML = `
        <style>${this.styles()}</style>
        <section class="joi-root${openClass}" aria-live="polite">
          <div class="joi-panel" part="panel">
            <header class="joi-panel__header">
              <div>
                <strong>Joi</strong>
                <span data-status>离线</span>
              </div>
              <button class="joi-icon-button" type="button" data-close aria-label="收起">×</button>
            </header>
            <div class="joi-stream" data-stream></div>
            <form class="joi-composer" data-form>
              <input data-input autocomplete="off" maxlength="500" placeholder="和 Joi 说点什么" />
              <button type="submit">发送</button>
            </form>
          </div>
          <button class="joi-speech" data-bubble type="button" aria-label="打开 Joi">
            <span data-bubble-text>我在这里。</span>
          </button>
          <div class="joi-pet" data-pet role="button" tabindex="0" aria-label="Joi 悬浮助手">
            <div class="joi-shadow"></div>
            <div class="joi-character" data-character>
              <div class="joi-body-wrap">
                <img class="joi-body" draggable="false" src="${this.assetBase}joi-body.png" alt="" />
              </div>
              <div class="joi-head-wrap" data-head>
                <img class="joi-head" draggable="false" src="${this.assetBase}joi-front-head.png" alt="" />
              </div>
              <div class="joi-face-pop" data-face-pop>
                <img draggable="false" src="${this.assetBase}joi-face-wink.png" alt="" />
              </div>
            </div>
          </div>
        </section>
      `;
      if (this.messages.length) this.paintMessages();
    }

    styles() {
      return `
        :host {
          --joi-coral: #d96f5f;
          --joi-coral-dark: #aa4e42;
          --joi-blue: #557f95;
          --joi-ink: #26343b;
          --joi-paper: #fffaf6;
          --joi-line: rgba(69, 47, 41, 0.16);
          --look-x: 0;
          --look-y: 0;
          --drag-x: 0;
          --drag-y: 0;
          --pet-w: 210px;
          --pet-h: 520px;
          position: fixed;
          inset: 0;
          z-index: 2147482800;
          pointer-events: none;
          font-family: ui-sans-serif, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
          color: var(--joi-ink);
        }

        * { box-sizing: border-box; }
        button, input { font: inherit; }

        .joi-root {
          position: absolute;
          left: 0;
          top: 0;
          width: var(--pet-w);
          height: var(--pet-h);
          pointer-events: none;
          user-select: none;
          touch-action: none;
          transform: translate3d(var(--joi-x, 0px), var(--joi-y, 0px), 0);
        }

        .joi-pet {
          position: absolute;
          inset: auto 0 0 0;
          width: var(--pet-w);
          height: var(--pet-h);
          border: 0;
          padding: 0;
          background: transparent;
          cursor: grab;
          pointer-events: auto;
          touch-action: none;
          outline: none;
        }

        .joi-pet:focus-visible .joi-character {
          filter: drop-shadow(0 0 0.5rem rgba(85, 127, 149, 0.42));
        }

        .joi-root.is-dragging .joi-pet { cursor: grabbing; }

        .joi-character {
          position: absolute;
          left: 50%;
          bottom: 0;
          width: 194px;
          height: 520px;
          transform: translateX(-50%) rotate(calc(var(--look-x) * 1.8deg));
          transform-origin: 50% 84%;
          transition: transform 120ms ease-out, filter 160ms ease;
          will-change: transform;
          pointer-events: none;
          filter: drop-shadow(0 18px 20px rgba(92, 58, 44, 0.16));
        }

        .joi-root.is-dragging .joi-character {
          animation: joi-drag-wiggle 520ms ease-in-out infinite;
          transform: translateX(-50%)
            rotate(calc((var(--drag-x) * 0.035deg) + (var(--look-x) * 2deg)))
            translate(calc(var(--drag-x) * 0.025px), calc(var(--drag-y) * 0.012px));
          filter: drop-shadow(0 24px 20px rgba(92, 58, 44, 0.2));
        }

        .joi-body-wrap,
        .joi-head-wrap,
        .joi-face-pop {
          position: absolute;
          left: 0;
          top: 0;
          width: 100%;
          pointer-events: none;
        }

        .joi-body-wrap {
          top: 0;
          height: 100%;
          transform: rotate(calc(var(--look-x) * 0.9deg));
          transform-origin: 48% 82%;
          transition: transform 180ms ease-out;
        }

        .joi-body {
          position: absolute;
          left: 0;
          top: 0;
          width: 194px;
          height: auto;
          -webkit-user-drag: none;
        }

        .joi-head-wrap {
          left: 27px;
          top: 1px;
          width: 132px;
          height: 158px;
          transform:
            translate(calc(var(--look-x) * 8px), calc(var(--look-y) * 6px))
            rotate(calc(var(--look-x) * 8deg));
          transform-origin: 52% 78%;
          transition: transform 90ms ease-out;
          will-change: transform;
        }

        .joi-root.is-dragging .joi-head-wrap {
          animation: joi-head-bob 420ms ease-in-out infinite;
          transform:
            translate(calc(var(--drag-x) * -0.018px), calc(var(--drag-y) * -0.012px))
            rotate(calc(var(--drag-x) * -0.035deg));
        }

        .joi-head {
          width: 100%;
          height: auto;
          -webkit-user-drag: none;
        }

        .joi-face-pop {
          left: 27px;
          top: 10px;
          width: 134px;
          opacity: 0;
          transform: scale(0.88) rotate(-4deg);
          transition: opacity 150ms ease, transform 150ms ease;
        }

        .joi-root.is-dragging .joi-face-pop,
        .joi-root.is-happy .joi-face-pop {
          opacity: 1;
          transform: scale(1) rotate(calc(var(--look-x) * 4deg));
        }

        .joi-face-pop img {
          width: 100%;
          height: auto;
          -webkit-user-drag: none;
        }

        .joi-shadow {
          position: absolute;
          left: 50%;
          bottom: -4px;
          width: 96px;
          height: 18px;
          transform: translateX(-50%);
          border-radius: 999px;
          background: rgba(93, 66, 58, 0.16);
          filter: blur(5px);
          pointer-events: none;
        }

        .joi-speech {
          position: absolute;
          right: 168px;
          bottom: 326px;
          min-width: 126px;
          max-width: 236px;
          padding: 10px 12px;
          border: 1px solid rgba(217, 111, 95, 0.24);
          border-radius: 14px 14px 4px 14px;
          background: rgba(255, 250, 246, 0.94);
          box-shadow: 0 14px 34px rgba(69, 47, 41, 0.14);
          color: var(--joi-ink);
          text-align: left;
          font-size: 13px;
          line-height: 1.35;
          pointer-events: auto;
          cursor: pointer;
          backdrop-filter: blur(14px);
        }

        .joi-speech span {
          display: -webkit-box;
          -webkit-line-clamp: 3;
          -webkit-box-orient: vertical;
          overflow: hidden;
        }

        .joi-panel {
          position: absolute;
          right: 176px;
          bottom: 18px;
          width: min(340px, calc(100vw - 32px));
          height: 412px;
          display: grid;
          grid-template-rows: auto 1fr auto;
          border: 1px solid var(--joi-line);
          border-radius: 8px;
          background:
            linear-gradient(180deg, rgba(255, 250, 246, 0.98), rgba(252, 244, 236, 0.96)),
            var(--joi-paper);
          box-shadow: 0 22px 56px rgba(69, 47, 41, 0.18);
          overflow: hidden;
          opacity: 0;
          transform: translateY(12px) scale(0.98);
          pointer-events: none;
          transition: opacity 170ms ease, transform 170ms ease;
          backdrop-filter: blur(18px);
        }

        .joi-root.is-open .joi-panel {
          opacity: 1;
          transform: translateY(0) scale(1);
          pointer-events: auto;
        }

        .joi-panel__header {
          display: flex;
          align-items: center;
          justify-content: space-between;
          min-height: 52px;
          padding: 12px 12px 10px 14px;
          border-bottom: 1px solid var(--joi-line);
        }

        .joi-panel__header strong {
          display: block;
          font-size: 15px;
          line-height: 1.1;
          letter-spacing: 0;
        }

        .joi-panel__header span {
          display: block;
          margin-top: 4px;
          color: #6f7777;
          font-size: 11px;
        }

        .joi-icon-button {
          width: 30px;
          height: 30px;
          border: 1px solid rgba(69, 47, 41, 0.12);
          border-radius: 8px;
          background: rgba(255, 255, 255, 0.62);
          color: #7c554e;
          cursor: pointer;
        }

        .joi-stream {
          display: flex;
          flex-direction: column;
          gap: 9px;
          padding: 12px;
          overflow: auto;
          scrollbar-width: thin;
        }

        .joi-message {
          max-width: 88%;
          padding: 9px 10px;
          border-radius: 8px;
          font-size: 13px;
          line-height: 1.45;
          word-break: break-word;
          box-shadow: 0 8px 18px rgba(69, 47, 41, 0.08);
        }

        .joi-message.user {
          align-self: flex-end;
          background: #d96f5f;
          color: white;
        }

        .joi-message.joi {
          align-self: flex-start;
          background: rgba(255, 255, 255, 0.78);
          border: 1px solid rgba(85, 127, 149, 0.18);
        }

        .joi-actions {
          display: flex;
          align-self: flex-start;
          gap: 8px;
          margin-top: -2px;
        }

        .joi-actions button {
          height: 30px;
          padding: 0 11px;
          border: 0;
          border-radius: 8px;
          background: var(--joi-coral);
          color: white;
          cursor: pointer;
          box-shadow: 0 8px 18px rgba(69, 47, 41, 0.1);
        }

        .joi-actions button.secondary {
          border: 1px solid rgba(69, 47, 41, 0.14);
          background: rgba(255, 255, 255, 0.72);
          color: #7c554e;
        }

        .joi-composer {
          display: grid;
          grid-template-columns: 1fr auto;
          gap: 8px;
          padding: 10px;
          border-top: 1px solid var(--joi-line);
          background: rgba(255, 255, 255, 0.5);
        }

        .joi-composer input {
          min-width: 0;
          height: 38px;
          padding: 0 11px;
          border: 1px solid rgba(69, 47, 41, 0.14);
          border-radius: 8px;
          background: rgba(255, 255, 255, 0.9);
          color: var(--joi-ink);
          outline: none;
        }

        .joi-composer input:focus {
          border-color: rgba(85, 127, 149, 0.56);
          box-shadow: 0 0 0 3px rgba(85, 127, 149, 0.14);
        }

        .joi-composer button {
          height: 38px;
          padding: 0 13px;
          border: 0;
          border-radius: 8px;
          background: var(--joi-blue);
          color: white;
          cursor: pointer;
        }

        @keyframes joi-drag-wiggle {
          0%, 100% { translate: 0 0; }
          50% { translate: 0 -4px; }
        }

        @keyframes joi-head-bob {
          0%, 100% { margin-top: 0; }
          50% { margin-top: -5px; }
        }

        @media (max-width: 640px) {
          :host {
            --pet-w: 164px;
            --pet-h: 406px;
          }

          .joi-character {
            width: 152px;
            height: 406px;
          }

          .joi-body { width: 152px; }

          .joi-head-wrap {
            left: 21px;
            width: 104px;
            height: 125px;
          }

          .joi-face-pop {
            left: 21px;
            width: 105px;
          }

          .joi-speech {
            right: 122px;
            bottom: 248px;
            min-width: 112px;
            max-width: 180px;
            font-size: 12px;
          }

          .joi-panel {
            right: 0;
            bottom: 418px;
            height: min(380px, calc(100vh - 446px));
          }
        }
      `;
    }

    cacheParts() {
      this.root = this.shadowRoot.querySelector(".joi-root");
      this.pet = this.shadowRoot.querySelector("[data-pet]");
      this.bubble = this.shadowRoot.querySelector("[data-bubble]");
      this.bubbleText = this.shadowRoot.querySelector("[data-bubble-text]");
      this.statusText = this.shadowRoot.querySelector("[data-status]");
      this.stream = this.shadowRoot.querySelector("[data-stream]");
      this.form = this.shadowRoot.querySelector("[data-form]");
      this.input = this.shadowRoot.querySelector("[data-input]");
      this.closeButton = this.shadowRoot.querySelector("[data-close]");
    }

    bindEvents() {
      window.removeEventListener("pointermove", this.handleWindowPointerMove);
      window.removeEventListener("resize", this.handleResize);
      window.addEventListener("pointermove", this.handleWindowPointerMove, { passive: true });
      window.addEventListener("resize", this.handleResize);

      this.pet.addEventListener("pointerdown", (event) => this.startDrag(event));
      this.pet.addEventListener("keydown", (event) => {
        if (event.key === "Enter" || event.key === " ") {
          event.preventDefault();
          this.togglePanel();
        }
      });
      this.bubble.addEventListener("click", () => this.togglePanel(true));
      this.closeButton.addEventListener("click", () => this.togglePanel(false));
      this.form.addEventListener("submit", (event) => this.submit(event));
      this.input.addEventListener("keydown", (event) => {
        if (event.key === "Enter" && !event.isComposing) this.submit(event);
      });
    }

    setInitialPosition() {
      const width = Number.parseFloat(getComputedStyle(this).getPropertyValue("--pet-w")) || this.state.width;
      const height = Number.parseFloat(getComputedStyle(this).getPropertyValue("--pet-h")) || this.state.height;
      this.state.width = width;
      this.state.height = height;
      if (!this.state.positioned) {
        this.state.x = Math.max(12, window.innerWidth - width - 26);
        this.state.y = Math.max(12, window.innerHeight - height - 10);
        this.state.positioned = true;
      } else {
        this.state.x = clamp(this.state.x, 8, window.innerWidth - this.state.width - 8);
        this.state.y = clamp(this.state.y, 8, window.innerHeight - this.state.height - 8);
      }
    }

    handleResize() {
      this.setInitialPosition();
      this.updatePosition();
    }

    connect() {
      window.clearTimeout(this.reconnectTimer);
      if (!this.coreUrl) return;
      try {
        this.socket?.close();
        this.socket = new WebSocket(this.coreUrl);
        this.setStatus("连接中");
        this.socket.onopen = () => {
          this.state.connected = true;
          this.setStatus("在线");
          this.say("我连上了。");
        };
        this.socket.onclose = () => {
          this.state.connected = false;
          this.setStatus("离线");
          this.reconnectTimer = window.setTimeout(() => this.connect(), 1800);
        };
        this.socket.onerror = () => {
          this.state.connected = false;
          this.setStatus("连接失败");
        };
        this.socket.onmessage = (event) => this.handleSocketMessage(event.data);
      } catch {
        this.setStatus("离线");
      }
    }

    handleSocketMessage(raw) {
      let payload;
      try {
        payload = JSON.parse(raw);
      } catch {
        return;
      }
      if (payload.method === "core.ready") {
        this.setStatus("在线");
        return;
      }
      if (payload.method !== "agent.event" || !payload.params) return;
      const event = payload.params;
      if (event.type === "user_message") return;
      const text = event.display_card?.summary || event.voice_line?.text || event.display_card?.title || "";
      if (!text) return;
      this.addMessage("joi", text);
      this.maybeRenderApproval(event);
      this.say(text);
      if (event.type === "approval_required") this.setMood("thinking");
      else if (event.type === "task_completed" || event.type === "runtime_final") this.setMood("happy");
      else this.setMood("idle");
    }

    submit(event) {
      event.preventDefault();
      const text = this.input.value.trim();
      if (!text) return;
      this.input.value = "";
      this.addMessage("user", text);
      this.togglePanel(true);
      this.say("收到。");
      this.setMood("thinking");
      if (this.socket?.readyState === WebSocket.OPEN) {
        this.socket.send(JSON.stringify({
          jsonrpc: "2.0",
          id: `web-${this.nextId++}`,
          method: "user.message",
          params: { text },
        }));
      } else {
        window.setTimeout(() => {
          this.addMessage("joi", "Joi Core 还没连上，我会继续尝试。");
          this.say("Joi Core 还没连上。");
          this.setMood("idle");
        }, 240);
      }
    }

    maybeRenderApproval(event) {
      if (event.type !== "approval_required") return;
      const approvalId = event.agent_state?.approval?.approval_id;
      if (!approvalId) return;
      const actions = document.createElement("div");
      actions.className = "joi-actions";

      const approve = document.createElement("button");
      approve.type = "button";
      approve.textContent = "允许";
      approve.addEventListener("click", () => this.resolveApproval(approvalId, true, actions));

      const deny = document.createElement("button");
      deny.type = "button";
      deny.textContent = "拒绝";
      deny.className = "secondary";
      deny.addEventListener("click", () => this.resolveApproval(approvalId, false, actions));

      actions.append(approve, deny);
      this.stream.append(actions);
      this.stream.scrollTop = this.stream.scrollHeight;
    }

    resolveApproval(approvalId, approved, actions) {
      actions.remove();
      this.addMessage("user", approved ? "允许执行" : "拒绝执行");
      if (this.socket?.readyState !== WebSocket.OPEN) return;
      this.socket.send(JSON.stringify({
        jsonrpc: "2.0",
        id: `approval-${this.nextId++}`,
        method: "approval.resolve",
        params: { approval_id: approvalId, approved },
      }));
    }

    addMessage(role, text) {
      this.messages.push({ role, text });
      if (this.messages.length > 40) this.messages.shift();
      this.paintMessages();
    }

    paintMessages() {
      if (!this.stream) return;
      this.stream.innerHTML = "";
      for (const message of this.messages) {
        const row = document.createElement("div");
        row.className = `joi-message ${message.role}`;
        row.textContent = message.text;
        this.stream.append(row);
      }
      this.stream.scrollTop = this.stream.scrollHeight;
    }

    say(text) {
      this.bubbleText.textContent = text.length > 86 ? `${text.slice(0, 86)}...` : text;
    }

    setStatus(text) {
      if (this.statusText) this.statusText.textContent = text;
    }

    setMood(mood) {
      this.root.classList.toggle("is-happy", mood === "happy");
      if (mood === "happy") {
        window.setTimeout(() => this.root?.classList.remove("is-happy"), 1400);
      }
    }

    togglePanel(force) {
      this.state.expanded = typeof force === "boolean" ? force : !this.state.expanded;
      this.root.classList.toggle("is-open", this.state.expanded);
      if (this.state.expanded) window.setTimeout(() => this.input?.focus(), 20);
    }

    startDrag(event) {
      if (event.button !== undefined && event.button !== 0) return;
      event.preventDefault();
      this.state.dragging = true;
      this.root.classList.add("is-dragging");
      try {
        this.pet.setPointerCapture(event.pointerId);
      } catch {
        // Some browser automation and older touch stacks do not support capture.
      }
      this.state.offsetX = event.clientX - this.state.x;
      this.state.offsetY = event.clientY - this.state.y;
      this.state.lastDragX = event.clientX;
      this.state.lastDragY = event.clientY;
      this.state.lastMoveAt = now();

      let cleaned = false;
      const onMove = (moveEvent) => {
        moveEvent.preventDefault?.();
        this.drag(moveEvent);
      };
      const onUp = (upEvent) => {
        if (cleaned) return;
        cleaned = true;
        try {
          this.pet.releasePointerCapture(upEvent.pointerId);
        } catch {
          // Ignore capture release on event stacks that never captured.
        }
        window.removeEventListener("pointermove", onMove);
        window.removeEventListener("pointerup", onUp);
        window.removeEventListener("pointercancel", onUp);
        window.removeEventListener("mousemove", onMove);
        window.removeEventListener("mouseup", onUp);
        this.endDrag();
      };
      window.addEventListener("pointermove", onMove, { passive: false });
      window.addEventListener("pointerup", onUp);
      window.addEventListener("pointercancel", onUp);
      window.addEventListener("mousemove", onMove, { passive: false });
      window.addEventListener("mouseup", onUp);
    }

    drag(event) {
      const elapsed = Math.max(16, now() - this.state.lastMoveAt);
      this.state.velocityX = ((event.clientX - this.state.lastDragX) / elapsed) * 1000;
      this.state.velocityY = ((event.clientY - this.state.lastDragY) / elapsed) * 1000;
      this.state.lastDragX = event.clientX;
      this.state.lastDragY = event.clientY;
      this.state.lastMoveAt = now();
      this.state.x = clamp(event.clientX - this.state.offsetX, 8, window.innerWidth - this.state.width - 8);
      this.state.y = clamp(event.clientY - this.state.offsetY, 8, window.innerHeight - this.state.height - 8);
      this.updatePosition();
      this.updateDragVars();
    }

    endDrag() {
      this.state.dragging = false;
      this.root.classList.remove("is-dragging");
      this.updateDragVars(true);
      if (!this.state.expanded) this.say("放这里也可以。");
    }

    handleWindowPointerMove(event) {
      this.state.pointerX = event.clientX;
      this.state.pointerY = event.clientY;
      if (this.state.dragging) return;
      const centerX = this.state.x + this.state.width * 0.5;
      const centerY = this.state.y + this.state.height * 0.22;
      const lookX = clamp((event.clientX - centerX) / 320, -1, 1);
      const lookY = clamp((event.clientY - centerY) / 260, -1, 1);
      this.root.style.setProperty("--look-x", lookX.toFixed(3));
      this.root.style.setProperty("--look-y", lookY.toFixed(3));
    }

    updatePosition() {
      this.root.style.setProperty("--joi-x", `${Math.round(this.state.x)}px`);
      this.root.style.setProperty("--joi-y", `${Math.round(this.state.y)}px`);
    }

    updateDragVars(reset = false) {
      const dragX = reset ? 0 : clamp(this.state.velocityX, -900, 900);
      const dragY = reset ? 0 : clamp(this.state.velocityY, -900, 900);
      this.root.style.setProperty("--drag-x", dragX.toFixed(1));
      this.root.style.setProperty("--drag-y", dragY.toFixed(1));
    }

  }

  if (!customElements.get("joi-floating-assistant")) {
    customElements.define("joi-floating-assistant", JoiFloatingAssistant);
  }
})();
