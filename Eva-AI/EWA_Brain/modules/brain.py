                        result = {"type": "answer", "text": message, "session_id": session_id,
                                  "memory": "saved" if changed else "skipped"}
                        save_turn(db, session_id, text, message)
                        return result
                    if short in NO:
                        clear_pending(db, session_id)
                        message = "Dobrze, nie zapisuję tej zmiany."
                        save_turn(db, session_id, text, message)
                        return {"type": "answer", "text": message, "session_id": session_id, "memory": "rejected"}
                    clear_pending(db, session_id)  # nowy temat
                elif kind == "clarification":
                    if short in NO:
                        clear_pending(db, session_id)
                        message = "Rozumiem. Doprecyzuj proszę nazwę projektu."
                        save_turn(db, session_id, text, message)
                        return {"type": "clarify", "text": message, "session_id": session_id, "memory": "none"}
                    if short in YES:
                        clear_pending(db, session_id)
                        project_id = payload.get("project_id", "")
                        alias = payload.get("alias", "")
                        source = payload.get("original", "")
                        if self.store.project(project_id) and _safe_alias_from_query(alias, source):
                            try:
                                self.store.apply({"action": "add_alias", "project_id": project_id,
                                                  "value": alias}, db, session_id)
                            except ValueError:
                                pass  # konflikt / za długi alias: bez trwałego wpisu
                        confirmed_note = (f"Użytkownik potwierdził interpretację wypowiedzi {source!r} "
                                          f"jako projektu {project_id!r}. Nie pytaj ponownie o to samo. "
                                          "Brak narzędzi do sterowania urządzeniami.")
                    else:
                        clear_pending(db, session_id)
            context = self.store.get_context(text, history)
            try:
                response = self.model_client(history, text, context, confirmed_note)
            except (OllamaError, ValueError, KeyError, TypeError) as exc:
                raise OllamaError(str(exc)) from exc
            answer = response.get("text", "").strip()
            if not answer:
                raise OllamaError("Model zwrócił pusty tekst")
            result = {"type": response.get("type", "answer"), "text": answer,
                      "session_id": session_id, "memory": "none"}
            if response.get("_ollama_timings"):
                result["timings"] = response["_ollama_timings"]
            if result["type"] == "clarify":
                project_id = response.get("clarification_project_id", "")
                interpretation = response.get("interpretation", "")
                if not self.store.project(project_id) and interpretation:
                    for proj in self.store.load()["projects"]:
                        if normal(proj["name"]) == normal(interpretation):
                            project_id = proj["id"]
                            break
                if not interpretation:
                    # Nie utrwalamy niejasnego pytania bez interpretacji.
                    result["type"] = "answer"
                else:
                    alias = response.get("clarification_alias", "")
                    if not alias and project_id:
                        guess = re.sub(r"^(podaj stan|pokaz stan|sprawdz stan|stan|status)\s+", "", normal(text)).strip(" .!?")
                        if _safe_alias_from_query(guess, text):
                            alias = guess
                    set_pending(db, session_id, "clarification", {
                        "original": text,
                        "interpretation": interpretation,
                        "project_id": project_id if self.store.project(project_id) else "",
                        "alias": alias,
                    })
            if result["type"] != "clarify":
                self._handle_memory_proposal(response, text, db, session_id, result)
            save_turn(db, session_id, text, result["text"])
            return result

    def _handle_memory_proposal(self, response: dict, question: str, db,
                                session_id: str, result: dict) -> None:
        action = response.get("memory_action", "none")
        if action not in {"add_fact", "replace_fact", "add_alias", "add_project"}:
            return
        value = response.get("memory_value", "").strip()
        pid = response.get("memory_project_id", "").strip()
        old = response.get("memory_old_value", "").strip()
        proposal = {"action": action, "project_id": pid, "value": value}
        if action == "replace_fact":
            proposal["old_value"] = old
        # Odrzuć automatycznie puste lub niewspierane propozycje.
        if not value or not pid or len(value) > 240:
            return
        project = self.store.project(pid)
        if action != "add_project" and not project:
            return
        if action == "add_project" and project:
            return
        if action == "replace_fact" and (not old or old not in project["facts"]):
            return
        if action == "add_fact" and not _grounded_in_user(value, question):
            return
        if action == "add_alias" and not _safe_alias_from_query(value, question):
            return
        # Blokuj propozycje na pytaniach, które nie przekazują faktów.
        if ("?" in question or QUESTION_START.search(normal(question)) or
                re.match(r"^(wlacz|wylacz|podaj|pokaz|sprawdz|otworz|zagraj|odtworz)\b", normal(question))):
            return
        auto = action == "add_fact" and _explicit_new_fact(question, value, project)
        # Sprostowania, niepewne fakty, aliasy bez potwierdzonej interpretacji
        # i nowe projekty zawsze wymagają potwierdzenia.
        if auto:
            try:
                changed, message = self.store.apply(proposal, db, session_id)
                result["memory"] = "saved" if changed else "skipped"
                if changed:
                    result["text"] = (result["text"].rstrip(" .") + ". "
                                      "Zapamiętałam tę informację o projekcie.")
            except ValueError:
                result["memory"] = "rejected"
            return
        label = {"add_fact": "Zapamiętać informację", "replace_fact": "Zastąpić wcześniejszy wpis",
                 "add_alias": "Dodać alias", "add_project": "Dodać nowy projekt"}[action]
        name = project["name"] if project else value
        prompt = f"{label} dla {name}: «{value}»? Odpowiedz tak lub nie."
        set_pending(db, session_id, "memory", proposal)
        result["type"] = "clarify"
        result["text"] = prompt
        result["memory"] = "pending_confirmation"

