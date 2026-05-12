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
      {
        command: "/it 015",
        label: "IT",
        description: "Consulta uma instrução de trabalho por código.",
        keywords: "it regra regras instrucao instrucoes codigo",
        template: "/it 015",
      },
      {
        command: "/colreg-lista",
        label: "COLREG lista",
        description: "Lista as regras RIEAM/COLREG disponíveis.",
        keywords: "colreg rieam lista regras navegacao",
        template: "/colreg-lista",
      },
      {
        command: "/colreg 19",
        label: "COLREG",
        description: "Consulta uma regra RIEAM/COLREG específica.",
        keywords: "colreg rieam regra navegacao nevoeiro luzes",
        template: "/colreg 19",
      },
      {
        command: "/planeamento",
        label: "Planeamento",
        description: "Lista todas as manobras no planeamento.",
        keywords: "planeamento manobras todas",
        template: "/planeamento",
      },
      {
        command: "/manobras-planeadas",
        label: "Planeadas",
        description: "Lista só as manobras já aprovadas.",
        keywords: "planeadas aprovadas manobras",
        template: "/manobras-planeadas",
      },
      {
        command: "/manobras-previstas",
        label: "Previstas",
        description: "Lista só as manobras ainda pendentes.",
        keywords: "previstas pendentes manobras",
        template: "/manobras-previstas",
      },
      {
        command: "/validar-manobra",
        label: "Validar manobra",
        description: "Valida uma manobra com checklist e histórico.",
        keywords: "validar manobra checklist historico histórico",
        template: joinLines([
          "/validar-manobra",
          "ID da manobra: ",
          "Ref: ",
          "Tipo de manobra: entrada | saída | mudança",
        ]),
      },
      {
        command: "/consultar-escala",
        label: "Consultar escala",
        description: "Mostra os dados básicos da escala.",
        keywords: "consultar escala ref",
        template: joinLines([
          "/consultar-escala",
          "Ref: ",
        ]),
      },
      {
        command: "/consultar-escala-custo",
        label: "Custo escala",
        description: "Mostra a escala com estimativa de custos.",
        keywords: "consultar custo escala faturacao",
        template: joinLines([
          "/consultar-escala-custo",
          "Ref: ",
        ]),
      },
      {
        command: "/consultar-manobra",
        label: "Consultar manobra",
        description: "Mostra os dados básicos da manobra.",
        keywords: "consultar manobra id ref tipo",
        template: joinLines([
          "/consultar-manobra",
          "ID da manobra: ",
          "Ref: ",
          "Tipo de manobra: entrada | saída | mudança",
        ]),
      },
      {
        command: "/consultar-manobra-custo",
        label: "Custo manobra",
        description: "Mostra a manobra com estimativa de custo.",
        keywords: "consultar custo manobra faturacao",
        template: joinLines([
          "/consultar-manobra-custo",
          "ID da manobra: ",
          "Ref: ",
          "Tipo de manobra: entrada | saída | mudança",
        ]),
      },
      {
        command: "/consultar-navio",
        label: "Consultar navio",
        description: "Mostra a ficha do navio conhecida no portal.",
        keywords: "consultar navio imo nome ficha",
        template: "/consultar-navio ",
      },
      {
        command: "/reportar-evento",
        label: "Reportar evento",
        description: "Regista uma ocorrência operacional.",
        keywords: "reportar evento ocorrencia tag local",
        template: "/reportar-evento TAG. LOCAL. DESCRIPTION",
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
          keywords: "abortar abortar-manobra cancelar manobra",
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
          command: "/porque",
          label: "Diagnóstico",
          description: "Mostra a ficha operacional da última resposta.",
          keywords: "diagnostico porque decisao regras resposta",
          template: "/porque",
        },
        {
          command: "/diagnostico",
          label: "Diagnóstico",
          description: "Alias para ver a ficha operacional da última resposta.",
          keywords: "diagnostico decisao regras resposta",
          template: "/diagnostico",
        },
        {
          command: "/debug",
          label: "Debug",
          description: "Mostra o diagnóstico técnico da última resposta.",
          keywords: "debug diagnostico decisao regras resposta",
          template: "/debug",
        },
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

    function commandToken(value) {
      return normalize(value).replace(/^\//, "").split(/\s+/)[0];
    }

    function replaceTemplateCommand(template, aliasCommand) {
      const cleanTemplate = String(template || aliasCommand);
      return cleanTemplate.replace(/^\/[^\s\n]*/, aliasCommand);
    }

    function aliasFrom(baseCommand, aliasCommand, label) {
      const baseToken = commandToken(baseCommand);
      const base = suggestions.find((item) => commandToken(item.command) === baseToken);
      if (!base) return;
      suggestions.push(Object.assign({}, base, {
        command: aliasCommand,
        label: label || base.label,
        keywords: [base.keywords, aliasCommand.replace(/^\//, "")].filter(Boolean).join(" "),
        template: replaceTemplateCommand(base.template, aliasCommand),
      }));
    }

    aliasFrom("/ondulacao", "/ondulação", "Ondulação");
    aliasFrom("/ondulacao", "/leitura-costeira", "Leitura costeira");
    aliasFrom("/validar-manobra", "/verificar-manobra", "Verificar manobra");
    aliasFrom("/validar-manobra", "/verificar", "Verificar");
    aliasFrom("/validar-manobra", "/validar", "Validar");
    aliasFrom("/validar-manobra", "/checklist-manobra", "Checklist manobra");
    aliasFrom("/reportar-evento", "/reportar_evento", "Reportar evento");
    aliasFrom("/registar-escala", "/nova-escala", "Nova escala");
    aliasFrom("/apagar-manobra", "/cancelar-manobra", "Cancelar manobra");
    aliasFrom("/abortar", "/abortar-manobra", "Abortar manobra");
    aliasFrom("/colreg-lista", "/colregs", "COLREG lista");
    aliasFrom("/colreg-lista", "/rieam-lista", "RIEAM lista");
    aliasFrom("/colreg", "/rieam", "RIEAM");
    aliasFrom("/colreg", "/regra-colreg", "Regra COLREG");
    aliasFrom("/colreg", "/regra-rieam", "Regra RIEAM");

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
