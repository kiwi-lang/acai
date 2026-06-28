"""Workflow routes — CRUD, validation, run, bundled agents/skills."""

from __future__ import annotations

import json
import logging
import os
import shutil
from typing import TYPE_CHECKING, Any, Callable

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from starlette.responses import StreamingResponse

if TYPE_CHECKING:
    from acai.orchestrator.routes import RouterDeps

log = logging.getLogger(__name__)


async def _json_body(request: Request) -> dict:
    try:
        return await request.json()
    except Exception:
        return {}


def create_workflows_router(
    deps: RouterDeps,
    *,
    make_audit: Callable[..., Any],
    extra_wf_dirs: list[str] | None = None,
) -> APIRouter:
    """Build the /workflows/* router."""

    router = APIRouter(tags=["workflows"])
    config = deps.config
    agent_store = deps.agent_store
    skill_store = deps.skill_store
    tool_registry = deps.tool_registry
    chat = deps.chat
    queue = deps.queue
    projects = deps.projects
    tracker = deps.tracker
    lb = deps.load_balancer
    workflows_dir = deps.workflows_dir
    _builtin_wf_dir = deps.builtin_wf_dir
    _extra_wf_dirs = extra_wf_dirs or []

    def _scan_wf_dir(directory: str, builtin: bool) -> list[dict]:
        results = []
        if not os.path.isdir(directory):
            return results
        for entry in sorted(os.listdir(directory)):
            defn = os.path.join(directory, entry, "definition.json")
            if not os.path.isfile(defn):
                continue
            try:
                with open(defn) as f:
                    spec = json.load(f)
                results.append({
                    "id": spec.get("id", entry),
                    "name": spec.get("name", entry),
                    "description": spec.get("description", ""),
                    "node_count": len(spec.get("nodes", [])),
                    "edge_count": len(spec.get("edges", [])),
                    "builtin": builtin,
                })
            except (json.JSONDecodeError, OSError):
                continue
        return results

    def _resolve_wf_dir(workflow_id: str) -> str:
        for base in (workflows_dir, _builtin_wf_dir):
            d = os.path.join(base, workflow_id)
            if os.path.isdir(d):
                return d
        return ""

    @router.get("/workflows/node-types")
    def get_node_types():
        from acai.tasks.nodes import all_types
        return [nt.to_dict() for nt in all_types()]

    @router.get("/workflows/agent-inputs/{agent_name}")
    def get_agent_template_inputs(agent_name: str):
        return {"agent": agent_name,
                "inputs": agent_store.template_inputs(agent_name)}

    @router.post("/workflows/resolve-pins")
    async def resolve_dynamic_pins(request: Request):
        from acai.tasks.nodes import get as get_nt
        body = await _json_body(request)
        node_type = body.get("node_type", "")
        data = body.get("data", {})
        spec = body.get("spec")
        nt = get_nt(node_type)
        if nt is None:
            return {"pins": []}
        td = tool_registry.mcp_definitions() if tool_registry else []
        dyn = nt.dynamic_pins(data, spec, tool_defs=td)
        return {"pins": [p.to_dict() for p in dyn]}

    @router.get("/workflows/tool-definitions")
    def get_tool_definitions():
        return tool_registry.mcp_definitions()

    @router.get("/workflows")
    def list_workflows():
        user_wfs = _scan_wf_dir(workflows_dir, builtin=False)
        user_ids = {w["id"] for w in user_wfs}
        builtin_wfs = [w for w in _scan_wf_dir(_builtin_wf_dir, builtin=True)
                       if w["id"] not in user_ids]
        for _wd in _extra_wf_dirs:
            for _w in _scan_wf_dir(_wd, builtin=True):
                if _w["id"] not in user_ids and _w["id"] not in {b["id"] for b in builtin_wfs}:
                    builtin_wfs.append(_w)
        return builtin_wfs + user_wfs

    @router.get("/workflows/{workflow_id}")
    def get_workflow(workflow_id: str):
        user_path = os.path.join(workflows_dir, workflow_id, "definition.json")
        if os.path.isfile(user_path):
            with open(user_path) as f:
                spec = json.load(f)
            spec["builtin"] = False
            return spec
        builtin_path = os.path.join(_builtin_wf_dir, workflow_id, "definition.json")
        if os.path.isfile(builtin_path):
            with open(builtin_path) as f:
                spec = json.load(f)
            spec["builtin"] = True
            return spec
        return JSONResponse({"error": "not found"}, status_code=404)

    @router.post("/workflows", status_code=201)
    async def save_workflow(request: Request):
        data = await _json_body(request)
        wf_id = data.get("id", "").strip()
        if not wf_id:
            return JSONResponse({"error": "id is required"}, status_code=400)
        data.setdefault("name", wf_id)
        wf_dir = os.path.join(workflows_dir, wf_id)
        os.makedirs(wf_dir, exist_ok=True)
        path = os.path.join(wf_dir, "definition.json")
        with open(path, "w") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        return data

    @router.put("/workflows/builtin/{workflow_id}")
    async def save_builtin_workflow(workflow_id: str, request: Request):
        data = await _json_body(request)
        data["id"] = workflow_id
        data.setdefault("name", workflow_id)
        wf_dir = os.path.join(_builtin_wf_dir, workflow_id)
        os.makedirs(wf_dir, exist_ok=True)
        path = os.path.join(wf_dir, "definition.json")
        with open(path, "w") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        return data

    @router.put("/workflows/{workflow_id}")
    async def update_workflow(workflow_id: str, request: Request):
        data = await _json_body(request)
        data["id"] = workflow_id
        data.setdefault("name", workflow_id)
        wf_dir = os.path.join(workflows_dir, workflow_id)
        os.makedirs(wf_dir, exist_ok=True)
        path = os.path.join(wf_dir, "definition.json")
        with open(path, "w") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        return data

    @router.delete("/workflows/{workflow_id}")
    def delete_workflow(workflow_id: str):
        wf_dir = os.path.join(workflows_dir, workflow_id)
        if os.path.isdir(wf_dir):
            shutil.rmtree(wf_dir)
        return {"deleted": True}

    @router.get("/workflows/{workflow_id}/agents")
    def list_workflow_agents(workflow_id: str):
        results = []
        for base in (os.path.join(workflows_dir, workflow_id),
                     os.path.join(_builtin_wf_dir, workflow_id)):
            agents_dir = os.path.join(base, "agents")
            if not os.path.isdir(agents_dir):
                continue
            for name in sorted(os.listdir(agents_dir)):
                def_path = os.path.join(agents_dir, name, "definition.json")
                if not os.path.isfile(def_path):
                    continue
                try:
                    with open(def_path) as f:
                        defn = json.load(f)
                    results.append({
                        "name": defn.get("name", name),
                        "description": defn.get("description", ""),
                        "provider": defn.get("provider", "auto"),
                        "output_format": defn.get("output_format", "text"),
                    })
                except (json.JSONDecodeError, OSError):
                    continue
            break
        return results

    @router.get("/workflows/{workflow_id}/skills")
    def list_workflow_skills(workflow_id: str):
        results = []
        for base in (os.path.join(workflows_dir, workflow_id),
                     os.path.join(_builtin_wf_dir, workflow_id)):
            skills_dir = os.path.join(base, "skills")
            if not os.path.isdir(skills_dir):
                continue
            for ns in sorted(os.listdir(skills_dir)):
                ns_dir = os.path.join(skills_dir, ns)
                if not os.path.isdir(ns_dir):
                    continue
                for name in sorted(os.listdir(ns_dir)):
                    tool_path = os.path.join(ns_dir, name, "tool.json")
                    if not os.path.isfile(tool_path):
                        continue
                    try:
                        with open(tool_path) as f:
                            defn = json.load(f)
                        results.append({
                            "qualified_name": f"{ns}.{name}",
                            "namespace": ns,
                            "name": defn.get("name", name),
                            "description": defn.get("description", ""),
                        })
                    except (json.JSONDecodeError, OSError):
                        continue
            break
        return results

    @router.post("/workflows/{workflow_id}/agents")
    async def create_workflow_agent(workflow_id: str, request: Request):
        data = await _json_body(request)
        name = data.get("name", "").strip()
        if not name:
            return JSONResponse({"error": "agent name required"}, status_code=400)
        wf_dir = os.path.join(workflows_dir, workflow_id)
        if not os.path.isdir(wf_dir):
            wf_dir = os.path.join(_builtin_wf_dir, workflow_id)
        agent_dir = os.path.join(wf_dir, "agents", name)
        os.makedirs(agent_dir, exist_ok=True)
        definition: dict = {
            "name": name,
            "description": data.get("description", ""),
            "role": data.get("role", "system"),
            "provider": data.get("provider", "auto"),
            "output_format": data.get("output_format", "messages"),
        }
        if data.get("model_overrides"):
            definition["model_overrides"] = data["model_overrides"]
        if data.get("tools"):
            definition["tools"] = data["tools"]
        if data.get("tool_permissions"):
            definition["tool_permissions"] = data["tool_permissions"]
        if data.get("resource_permissions"):
            definition["resource_permissions"] = data["resource_permissions"]
        if data.get("context_sources"):
            definition["context_sources"] = data["context_sources"]
        if "max_iterations" in data:
            definition["max_iterations"] = data["max_iterations"]
        if "approval_required" in data:
            definition["approval_required"] = data["approval_required"]
        if "uses_sandbox" in data:
            definition["uses_sandbox"] = data["uses_sandbox"]
        if data.get("tags"):
            definition["tags"] = data["tags"]
        if data.get("avatar"):
            definition["avatar"] = data["avatar"]
        if data.get("scope"):
            definition["scope"] = data["scope"]
        with open(os.path.join(agent_dir, "definition.json"), "w") as f:
            json.dump(definition, f, indent=2)
        template = data.get("system_template", "")
        if template:
            with open(os.path.join(agent_dir, "system.j2"), "w") as f:
                f.write(template)
        return {"created": True, "name": name}

    @router.get("/workflows/{workflow_id}/agents/{agent_name}")
    def get_workflow_agent(workflow_id: str, agent_name: str):
        for base in (os.path.join(workflows_dir, workflow_id),
                     os.path.join(_builtin_wf_dir, workflow_id)):
            agent_dir = os.path.join(base, "agents", agent_name)
            def_path = os.path.join(agent_dir, "definition.json")
            if os.path.isfile(def_path):
                with open(def_path) as f:
                    defn = json.load(f)
                tpl_path = os.path.join(agent_dir, "system.j2")
                template = ""
                if os.path.isfile(tpl_path):
                    with open(tpl_path) as f:
                        template = f.read()
                defn["system_template_content"] = template
                return defn
        return JSONResponse({"error": "not found"}, status_code=404)

    _DEFAULT_SKILL_CODE = (
        "#!/usr/bin/env python3\n"
        "import json, sys\n\n"
        "def main():\n"
        '    args = json.loads(sys.argv[1]) if len(sys.argv) > 1 else {}\n'
        '    result = {"status": "ok"}\n'
        "    print(json.dumps(result))\n\n"
        'if __name__ == "__main__":\n'
        "    main()\n"
    )

    @router.post("/workflows/{workflow_id}/skills")
    async def create_workflow_skill(workflow_id: str, request: Request):
        data = await _json_body(request)
        ns = data.get("namespace", "").strip()
        name = data.get("name", "").strip()
        if not ns or not name:
            return JSONResponse({"error": "namespace and name required"}, status_code=400)
        wf_dir = os.path.join(workflows_dir, workflow_id)
        if not os.path.isdir(wf_dir):
            wf_dir = os.path.join(_builtin_wf_dir, workflow_id)
        skill_dir = os.path.join(wf_dir, "skills", ns, name)
        os.makedirs(skill_dir, exist_ok=True)
        params = data.get("parameters")
        if isinstance(params, str):
            try:
                params = json.loads(params)
            except (json.JSONDecodeError, TypeError):
                params = None
        if not params:
            params = {"type": "object", "properties": {}, "required": []}
        tool_def = {
            "name": name,
            "description": data.get("description", ""),
            "parameters": params,
        }
        with open(os.path.join(skill_dir, "tool.json"), "w") as f:
            json.dump(tool_def, f, indent=2)
        code = data.get("code", "").strip() or _DEFAULT_SKILL_CODE
        with open(os.path.join(skill_dir, "run.py"), "w") as f:
            f.write(code)
        readme = data.get("readme", "").strip()
        if readme:
            with open(os.path.join(skill_dir, "README.md"), "w") as f:
                f.write(readme)
        requirements = data.get("requirements", "").strip()
        if requirements:
            with open(os.path.join(skill_dir, "requirements.txt"), "w") as f:
                f.write(requirements)
        return {"created": True, "qualified_name": f"{ns}.{name}"}

    @router.get("/workflows/{workflow_id}/skills/{namespace}/{skill_name}")
    def get_workflow_skill(workflow_id: str, namespace: str, skill_name: str):
        for base in (os.path.join(workflows_dir, workflow_id),
                     os.path.join(_builtin_wf_dir, workflow_id)):
            skill_dir = os.path.join(base, "skills", namespace, skill_name)
            tool_path = os.path.join(skill_dir, "tool.json")
            if os.path.isfile(tool_path):
                with open(tool_path) as f:
                    defn = json.load(f)
                code = ""
                run_path = os.path.join(skill_dir, "run.py")
                if os.path.isfile(run_path):
                    with open(run_path) as f:
                        code = f.read()
                readme = ""
                readme_path = os.path.join(skill_dir, "README.md")
                if os.path.isfile(readme_path):
                    with open(readme_path) as f:
                        readme = f.read()
                requirements = ""
                req_path = os.path.join(skill_dir, "requirements.txt")
                if os.path.isfile(req_path):
                    with open(req_path) as f:
                        requirements = f.read()
                return {
                    "namespace": namespace,
                    "name": skill_name,
                    "qualified_name": f"{namespace}.{skill_name}",
                    "description": defn.get("description", ""),
                    "parameters": defn.get("parameters", {}),
                    "code": code,
                    "readme": readme,
                    "requirements": requirements,
                }
        return JSONResponse({"error": "not found"}, status_code=404)

    @router.post("/workflows/validate")
    async def validate_workflow_spec(request: Request):
        from acai.tasks.typecheck import typecheck
        spec = await _json_body(request)
        wf_id = spec.get("id", "")
        wf_dir = _resolve_wf_dir(wf_id) if wf_id else ""
        td = tool_registry.mcp_definitions() if tool_registry else []
        extra_dirs = []
        if wf_dir:
            agents_sub = os.path.join(wf_dir, "agents")
            if os.path.isdir(agents_sub):
                extra_dirs.append(agents_sub)
        with agent_store.scoped(*extra_dirs):
            diags = typecheck(
                spec, tool_defs=td,
                agent_store=agent_store, workflow_dir=wf_dir,
            )
        errors = [d for d in diags if d.get("severity") == "error"]
        warnings = [d for d in diags if d.get("severity") == "warning"]
        return {
            "diagnostics": diags,
            "errors": errors,
            "warnings": warnings,
            "valid": len(errors) == 0,
        }

    @router.post("/workflows/{workflow_id}/validate")
    def validate_workflow_endpoint(workflow_id: str):
        from acai.tasks.typecheck import typecheck
        user_path = os.path.join(workflows_dir, workflow_id, "definition.json")
        builtin_path = os.path.join(_builtin_wf_dir, workflow_id, "definition.json")
        path = user_path if os.path.isfile(user_path) else builtin_path
        if not os.path.isfile(path):
            return JSONResponse({"error": "not found"}, status_code=404)
        with open(path) as f:
            spec = json.load(f)
        td = tool_registry.mcp_definitions() if tool_registry else []
        wf_dir = os.path.dirname(path)
        extra_dirs = []
        agents_sub = os.path.join(wf_dir, "agents")
        if os.path.isdir(agents_sub):
            extra_dirs.append(agents_sub)
        with agent_store.scoped(*extra_dirs):
            diags = typecheck(
                spec, tool_defs=td,
                agent_store=agent_store, workflow_dir=wf_dir,
            )
        errors = [d for d in diags if d.get("severity") == "error"]
        warnings = [d for d in diags if d.get("severity") == "warning"]
        return {
            "diagnostics": diags,
            "errors": errors,
            "warnings": warnings,
            "valid": len(errors) == 0,
        }

    @router.post("/workflows/{workflow_id}/run")
    async def run_workflow(workflow_id: str, request: Request):
        import traceback as _tb
        from acai.tasks import DynamicGraph

        wf_dir = os.path.join(workflows_dir, workflow_id)
        path = os.path.join(wf_dir, "definition.json")
        if not os.path.isfile(path):
            wf_dir = os.path.join(_builtin_wf_dir, workflow_id)
            path = os.path.join(wf_dir, "definition.json")
        if not os.path.isfile(path):
            return JSONResponse({"error": "workflow not found"}, status_code=404)
        with open(path) as f:
            spec = json.load(f)

        data = await _json_body(request)
        message = data.get("message", "")
        conversation_raw = data.get("conversation", "")
        test_mode = data.get("test", False)
        test_conversation = data.get("test_conversation", [])

        conversation_preview = ""
        conversation_id = ""

        if not test_mode:
            if isinstance(conversation_raw, str) and conversation_raw.strip().startswith("["):
                conversation_preview = conversation_raw
            elif isinstance(conversation_raw, list):
                conversation_preview = json.dumps(conversation_raw, ensure_ascii=False)
            elif conversation_raw:
                conversation_id = conversation_raw

            if not conversation_id:
                meta = chat.create(
                    title=f"Workflow: {spec.get('name', workflow_id)}"[:80],
                    agent="default",
                )
                conversation_id = meta.id

            if message:
                chat.append(conversation_id, {"role": "user", "content": message})

        work = {
            "message": message,
            "conversation": conversation_id,
            "conversation_preview": conversation_preview,
            "workflow_spec": spec,
            "workflow_dir": wf_dir,
            "stream_id": conversation_id or f"test-{workflow_id}",
        }

        if test_conversation and isinstance(test_conversation, list):
            work["test_conversation"] = test_conversation

        def _sse(event: str, data_payload: dict) -> str:
            return f"event: {event}\ndata: {json.dumps(data_payload, ensure_ascii=False)}\n\n"

        audit = make_audit(
            "workflow/run", workflow=workflow_id,
            workflow_name=spec.get("name", workflow_id),
            conversation=conversation_id,
        )

        async def generate():
            yield _sse("meta", {"conversation": conversation_id})
            try:
                async with lb.acquire() as worker:
                    audit.record("worker.acquired", phase="server", worker=worker.url)
                    graph = DynamicGraph.from_work(
                        worker, work,
                        agent_store=agent_store,
                        chat=chat,
                        config=config,
                        tracker=tracker,
                        projects=projects,
                        tool_registry=tool_registry,
                        audit=audit,
                    )
                    async for event in graph.run(work):
                        yield _sse(
                            event.get("event_type", "message"),
                            event.get("data", {}),
                        )
            except TimeoutError:
                audit.record("error", phase="server", error="worker timeout")
                yield _sse("error", {"message": "No worker available (timeout)."})
            except Exception as exc:
                log.exception("workflow run error")
                audit.record("error", phase="server", error=str(exc))
                yield _sse("error", {
                    "message": f"{type(exc).__name__}: {exc}",
                    "traceback": _tb.format_exc(),
                })
            finally:
                audit.finalize()
                summary = audit.client_summary()
                if summary.get("request_id"):
                    yield _sse("audit_complete", summary)

        return StreamingResponse(
            generate(),
            media_type="text/event-stream",
            headers={"X-Conversation": conversation_id},
        )

    return router
