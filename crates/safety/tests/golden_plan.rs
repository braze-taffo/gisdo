use gisdo_domain::TaskPlan;
use gisdo_safety::{ToolRegistry, validate_plan};

#[test]
fn golden_clip_plan_matches_the_embedded_510_tool_registry() {
    let inventory = include_str!("../../../fixtures/arcgis_tool_inventory_510.json");
    let registry = ToolRegistry::builtin()
        .with_arcgis_inventory(inventory)
        .unwrap();
    assert_eq!(registry.len(), 518, "510 ArcPy tools plus 8 core tools");
    let plan: TaskPlan =
        serde_json::from_str(include_str!("../../../fixtures/golden_plan.json")).unwrap();
    let order = validate_plan(&plan, &registry, false).unwrap();
    assert_eq!(order, ["project_buildings", "clip_conghua"]);
}
