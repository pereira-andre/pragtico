(function () {
  function normalize(value) {
    return String(value || "")
      .normalize("NFD")
      .replace(/[\u0300-\u036f]/g, "")
      .toLowerCase()
      .trim();
  }

  function joinLines(lines) {
    return lines.join("\n");
  }

  function buildSuggestions(role) {
    const cleanRole = normalize(role);
    const suggestions = [
      {
        command: "/help",
        label: "Ajuda",
        description: "Mostra a lista de comandos disponíveis.",
        keywords: "ajuda comandos",
        template: "/help",
      },
      {
        command: "/avisos-locais",
        label: "Avisos locais",
        description: "Consulta os avisos locais em vigor.",
        keywords: "aviso local navegacao",
        template: "/avisos-locais",
      },
      {
        command: "/ondulacao",
        label: "Ondulação",
        description: "Mostra a leitura costeira atual.",
        keywords: "ondas costa mar",
        template: "/ondulacao",
      },
      {
        command: "/mares hoje",
        label: "Marés",
        description: "Mostra as marés do dia.",
        keywords: "mare hoje tabela",
        template: "/mares hoje",
      },
      {
        command: "/meteorologia hoje",
        label: "Meteorologia",
        description: "Mostra a previsão meteorológica do dia.",
        keywords: "tempo previsao meteo",
        template: "/meteorologia hoje",
      },
      {
        command: "/regras",
        label: "Regras",
        description: "Lista os códigos de regras e instruções disponíveis.",
        keywords: "regras codigos instrucoes it",
        template: "/regras",
      },
      {
        command: "/regra 015",
        label: "Regra",
        description: "Consulta uma regra ou instrução por código.",
        keywords: "regra regras instrucao instrucoes it codigo",
        template: "/regra 015",
      },
    ];

    if (cleanRole === "admin" || cleanRole === "agente") {
      suggestions.push(
        {
          command: "/registar-escala",
          label: "Registar escala",
          description: "Insere o template completo de criação da escala.",
          keywords: "nova escala entrada navio",
          template: joinLines([
            "/registar-escala",
            "Nome do navio: ",
            "ETA de chegada: DD/MM/AAAA, HH:MM",
            "Cais previsto: ",
            "Último porto: ",
            "Próximo destino: ",
            "IMO: ",
            "Indicativo: ",
            "Bandeira: ",
            "Tipo de navio: ",
            "LOA (m): ",
            "Boca (m): ",
            "GT (t): ",
            "DWT (t): ",
            "Calado máximo (m): ",
            "Bow thruster: sim | não | desconhecido",
            "Stern thruster: sim | não | desconhecido",
            "Observações: ",
          ]),
        },
        {
          command: "/criar-manobra",
          label: "Criar manobra",
          description: "Preenche o template para saída ou mudança.",
          keywords: "criar manobra saida mudanca",
          template: joinLines([
            "/criar-manobra",
            "Ref: ",
            "Tipo de manobra: saída | mudança",
            "Hora prevista: DD/MM/AAAA, HH:MM",
            "Destino: ",
            "Calado: ",
            "Rebocadores: ",
            "Restrições: daylight, gas, estrategico",
            "Observações: ",
            "Nota: a origem segue automaticamente o último local conhecido do navio.",
          ]),
        },
        {
          command: "/apagar-manobra",
          label: "Apagar manobra",
          description: "Remove uma manobra planeada.",
          keywords: "apagar manobra eliminar",
          template: joinLines([
            "/apagar-manobra",
            "ID da manobra: ",
          ]),
        }
      );
    }

    if (cleanRole === "admin" || cleanRole === "agente") {
      suggestions.push({
        command: "/editar-manobra",
        label: "Editar manobra",
        description: "Altera o planeamento de uma manobra.",
        keywords: "editar manobra alterar planeamento",
        template: joinLines([
          "/editar-manobra",
          "ID da manobra: ",
          "Ref: ",
          "Tipo de manobra: entrada | saída | mudança",
          "Hora prevista: DD/MM/AAAA, HH:MM",
          "Origem: ",
          "Destino: ",
          "Calado: ",
          "Rebocadores: ",
          "Restrições: daylight, gas, estrategico",
          "Observações: ",
          "Motivo da alteração: ",
        ]),
      });
    }

    if (cleanRole === "admin" || cleanRole === "piloto") {
      suggestions.push(
        {
          command: "/aprovar",
          label: "Aprovar manobra",
          description: "Aprova uma manobra pendente.",
          keywords: "aprovar manobra",
          template: joinLines([
            "/aprovar",
            "ID da manobra: ",
            "Ref: ",
            "Tipo de manobra: entrada | saída | mudança",
            "Observações: ",
          ]),
        },
        {
          command: "/registar-manobra",
          label: "Registar manobra",
          description: "Preenche o registo com início, fim e calado.",
          keywords: "registar manobra inicio fim calado",
          template: joinLines([
            "/registar-manobra",
            "ID da manobra: ",
            "Ref: ",
            "Tipo de manobra: entrada | saída | mudança",
            "Início da manobra: DD/MM/AAAA, HH:MM",
            "Fim da manobra: DD/MM/AAAA, HH:MM",
            "Calado: ",
            "Observações: ",
          ]),
        },
        {
          command: "/editar-registo-manobra",
          label: "Editar registo",
          description: "Revê um registo já executado.",
          keywords: "editar registo manobra correcao",
          template: joinLines([
            "/editar-registo-manobra",
            "ID da manobra: ",
            "Ref: ",
            "Tipo de manobra: entrada | saída | mudança",
            "Início da manobra: DD/MM/AAAA, HH:MM",
            "Fim da manobra: DD/MM/AAAA, HH:MM",
            "Calado: ",
            "Observações: ",
            "Motivo da alteração: ",
          ]),
        },
        {
          command: "/abortar",
          label: "Abortar manobra",
          description: "Cancela ou aborta a manobra indicada.",
          keywords: "abortar cancelar manobra",
          template: joinLines([
            "/abortar",
            "ID da manobra: ",
            "Ref: ",
            "Tipo de manobra: entrada | saída | mudança",
            "Motivo: ",
          ]),
        }
      );
    }

    if (cleanRole === "admin" || cleanRole === "agente") {
      suggestions.push(
        {
          command: "/editar-escala",
          label: "Editar escala",
          description: "Atualiza os dados da escala.",
          keywords: "editar escala referencia",
          template: joinLines([
            "/editar-escala",
            "Ref: ",
            "Nome do navio: ",
            "ETA de chegada: DD/MM/AAAA, HH:MM",
            "Cais previsto: ",
            "Último porto: ",
            "Próximo destino: ",
            "IMO: ",
            "Indicativo: ",
            "Bandeira: ",
            "Tipo de navio: ",
            "LOA (m): ",
            "Boca (m): ",
            "GT (t): ",
            "DWT (t): ",
            "Calado máximo (m): ",
            "Bow thruster: sim | não | desconhecido",
            "Stern thruster: sim | não | desconhecido",
            "Observações: ",
          ]),
        },
        {
          command: "/apagar-escala",
          label: "Apagar escala",
          description: "Remove a escala pela referência.",
          keywords: "apagar escala eliminar ref",
          template: joinLines([
            "/apagar-escala",
            "Ref: ",
          ]),
        }
      );
    }

    if (cleanRole === "admin") {
      suggestions.push(
        {
          command: "/apagar-registo-manobra",
          label: "Apagar registo",
          description: "Apaga o registo executado de uma manobra.",
          keywords: "apagar registo manobra",
          template: joinLines([
            "/apagar-registo-manobra",
            "ID da manobra: ",
            "Ref: ",
            "Tipo de manobra: entrada | saída | mudança",
          ]),
        }
      );
    }

    return suggestions;
  }

  function attach(options) {
    const textarea = options && options.textarea;
    const panel = options && options.panel;
    const suggestions = buildSuggestions(options && options.role);
    if (!textarea || !panel) {
      return { handleKeydown() { return false; }, refresh() {}, close() {} };
    }

    let matches = [];
    let activeIndex = 0;

    function close() {
      matches = [];
      activeIndex = 0;
      panel.innerHTML = "";
      panel.classList.add("hidden");
    }

    function currentQuery() {
      return String(textarea.value || "").trim();
    }

    function shouldOpen(value) {
      const trimmed = String(value || "").trimStart();
      return trimmed.startsWith("/") && !trimmed.includes("\n");
    }

    function filterSuggestions() {
      if (!shouldOpen(textarea.value)) return [];
      const query = normalize(currentQuery().split("\n")[0]);
      const token = query.startsWith("/") ? query.slice(1) : query;
      return suggestions.filter((item) => {
        const command = normalize(item.command).replace(/^\//, "");
        const label = normalize(item.label);
        const description = normalize(item.description);
        const keywords = normalize(item.keywords);
        return !token
          || command.startsWith(token)
          || label.includes(token)
          || description.includes(token)
          || keywords.includes(token);
      }).slice(0, 8);
    }

    function render() {
      matches = filterSuggestions();
      if (!matches.length) {
        close();
        return;
      }

      panel.innerHTML = "";
      panel.classList.remove("hidden");
      matches.forEach((item, index) => {
        const button = document.createElement("button");
        button.type = "button";
        button.className = "chat-command-item" + (index === activeIndex ? " active" : "");
        button.dataset.index = String(index);
        button.innerHTML =
          "<strong>" + item.command + "</strong>" +
          "<span>" + item.label + "</span>" +
          "<small>" + item.description + "</small>";
        panel.appendChild(button);
      });
      const active = panel.querySelector(".chat-command-item.active");
      if (active) active.scrollIntoView({ block: "nearest" });
    }

    function apply(index) {
      const item = matches[index];
      if (!item) return false;
      textarea.value = item.template;
      textarea.dispatchEvent(new Event("input", { bubbles: true }));
      close();
      textarea.focus();
      textarea.setSelectionRange(textarea.value.length, textarea.value.length);
      return true;
    }

    function move(delta) {
      if (!matches.length) return false;
      activeIndex = (activeIndex + delta + matches.length) % matches.length;
      render();
      return true;
    }

    panel.addEventListener("mousedown", (event) => event.preventDefault());
    panel.addEventListener("click", (event) => {
      const target = event.target.closest("[data-index]");
      if (!target) return;
      apply(Number(target.dataset.index || 0));
    });

    textarea.addEventListener("input", () => {
      activeIndex = 0;
      render();
    });
    textarea.addEventListener("focus", () => render());
    textarea.addEventListener("blur", () => window.setTimeout(close, 120));

    return {
      handleKeydown(event) {
        if (panel.classList.contains("hidden") || !matches.length) return false;
        if (event.key === "ArrowDown") {
          event.preventDefault();
          return move(1);
        }
        if (event.key === "ArrowUp") {
          event.preventDefault();
          return move(-1);
        }
        if (event.key === "Enter" || event.key === "Tab") {
          event.preventDefault();
          return apply(activeIndex);
        }
        if (event.key === "Escape") {
          event.preventDefault();
          close();
          return true;
        }
        return false;
      },
      refresh() {
        activeIndex = 0;
        render();
      },
      close,
    };
  }

  window.PRAGticoChatCommandAutocomplete = {
    attach,
    buildSuggestions,
  };
})();
