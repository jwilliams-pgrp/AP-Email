from pathlib import Path
import json


ROOT = Path(__file__).resolve().parents[1]


def test_nonprod_deploy_packages_local_package_as_wheel() -> None:
    script = (ROOT / "deploy-azure-apps-nonprod.ps1").read_text(encoding="utf-8")

    assert "python -m pip wheel --no-deps --wheel-dir $wheelStage $repoRoot" in script
    assert 'Add-Content -LiteralPath (Join-Path $functionStage "requirements.txt") -Value "wheels/$($localPackageWheels[0].Name)"' in script
    assert '$entryName = $relativePath -replace "\\\\", "/"' in script
    assert "New-FunctionZipFromDirectory -SourceDirectory $functionStage -DestinationPath $functionZip" in script
    assert "--build-remote true" in script
    assert 'Copy-DirectoryContents -Source (Join-Path $repoRoot "src\\ap_automation")' not in script
    assert "ap_automation_vendor.zip" not in script
    assert ".python_packages" not in script


def test_function_app_does_not_bootstrap_vendored_package_paths() -> None:
    function_app = (ROOT / "function_app.py").read_text(encoding="utf-8")

    assert "ap_automation_vendor.zip" not in function_app
    assert ".python_packages" not in function_app
    assert "sys.path.insert(0, str(script_root))" in function_app


def test_function_app_exposes_asset_custom_workflow_routes() -> None:
    function_app = (ROOT / "function_app.py").read_text(encoding="utf-8")

    assert '@app.route(route="workflow/asset-custom", methods=["GET"]' in function_app
    assert '@app.route(route="workflow/asset-custom", methods=["POST"]' in function_app
    assert '@app.route(route="workflow/asset-custom/{asset_custom_id}", methods=["PATCH"]' in function_app
    assert '@app.route(route="workflow/asset-custom/{asset_custom_id}", methods=["DELETE"]' in function_app
    assert "service.workflow_asset_custom" in function_app
    assert "service.create_workflow_asset_custom(_json_body(req))" in function_app
    assert 'service.update_workflow_asset_custom(req.route_params["asset_custom_id"], _json_body(req))' in function_app
    assert 'service.delete_workflow_asset_custom(req.route_params["asset_custom_id"])' in function_app


def test_function_app_config_uses_identity_without_pythonpath_workaround() -> None:
    bicep = (ROOT / "infra" / "main.bicep").read_text(encoding="utf-8")

    assert "AZURE_CLIENT_ID: identity.properties.clientId" in bicep
    assert "PYTHONPATH:" not in bicep


def test_static_web_app_links_function_backend() -> None:
    bicep = (ROOT / "infra" / "main.bicep").read_text(encoding="utf-8")

    assert "Microsoft.Web/staticSites/linkedBackends@2023-12-01" in bicep
    assert "parent: staticWebApp" in bicep
    assert "name: 'functionApp'" in bicep
    assert "backendResourceId: functionApp.id" in bicep
    assert "region: location" in bicep


def test_static_web_app_config_requires_custom_user_role() -> None:
    config = json.loads((ROOT / "staticwebapp.config.json").read_text(encoding="utf-8"))
    routes = {route["route"]: route for route in config["routes"]}

    protected_routes = [routes["/api/*"], routes["/*"]]
    assert [route["allowedRoles"] for route in protected_routes] == [["user"], ["user"]]
    assert all("authenticated" not in route["allowedRoles"] for route in protected_routes)
    assert config["responseOverrides"]["401"] == {
        "redirect": "/.auth/login/aad",
        "statusCode": 302,
    }


def test_function_host_keeps_default_api_route_prefix() -> None:
    host_json = (ROOT / "host.json").read_text(encoding="utf-8")

    assert "routePrefix" not in host_json


def test_nonprod_deploy_validates_static_web_app_backend() -> None:
    script = (ROOT / "deploy-azure-apps-nonprod.ps1").read_text(encoding="utf-8")

    assert "az staticwebapp backends show" in script
    assert "--name $staticWebAppName" in script
    assert "--resource-group $ResourceGroup" in script
    assert "$linkedBackendRecord.backendResourceId" in script
    assert "$linkedBackendRecord.properties.backendResourceId" in script
    assert "$expectedFunctionAppId" in script
