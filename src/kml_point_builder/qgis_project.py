"""Create a QField-ready QGIS project using PyQGIS, not hand-written XML."""

from __future__ import annotations

import os
import sys
from pathlib import Path

from qgis.core import (
    QgsApplication,
    QgsAttributeEditorContainer,
    QgsAttributeEditorField,
    QgsAttributeEditorRelation,
    QgsDefaultValue,
    QgsEditFormConfig,
    QgsEditorWidgetSetup,
    QgsProject,
    QgsRelation,
    QgsVectorLayer,
)


def layer(name: str, gpkg_name: str) -> QgsVectorLayer:
    result = QgsVectorLayer(f"{gpkg_name}|layername={name}", name, "ogr")
    if not result.isValid():
        raise RuntimeError(f"QGIS could not load {name} from {gpkg_name}.")
    return result


def main() -> None:
    project_path = Path(sys.argv[1]).resolve()
    gpkg_name = sys.argv[2]
    os.chdir(project_path.parent)
    app = QgsApplication([], False)
    app.initQgis()
    try:
        project = QgsProject.instance()
        project.setFileName(str(project_path))
        project.setPresetHomePath(str(project_path.parent))
        project.writeEntry("Paths", "Absolute", False)
        parcels = layer("parcels", gpkg_name)
        attachments = layer("parcel_attachments", gpkg_name)
        points = layer("reference_points", gpkg_name)
        project.addMapLayers([parcels, attachments, points])

        relation = QgsRelation()
        relation.setId("parcel_photos")
        relation.setName("Photos")
        relation.setReferencedLayer(parcels.id())
        relation.setReferencingLayer(attachments.id())
        relation.addFieldPair("parcel_id", "parcel_id")
        relation.setStrength(QgsRelation.Composition)
        project.relationManager().addRelation(relation)

        photo_index = attachments.fields().indexOf("photo_path")
        attachments.setEditorWidgetSetup(photo_index, QgsEditorWidgetSetup("ExternalResource", {
            "DocumentViewer": 1, "DocumentViewerContent": 1,
            "RelativeStorage": 1, "DefaultRoot": "DCIM",
        }))
        attachments.setDefaultValueDefinition(
            attachments.fields().indexOf("attachment_uid"), QgsDefaultValue("uuid('WithoutBraces')")
        )
        attachment_form = attachments.editFormConfig()
        attachment_form.setLayout(QgsEditFormConfig.TabLayout)
        attachment_form.invisibleRootContainer().clear()
        attachment_container = QgsAttributeEditorContainer("Photo", attachment_form.invisibleRootContainer())
        attachment_form.invisibleRootContainer().addChildElement(attachment_container)
        for field in ("photo_path", "notes"):
            attachment_container.addChildElement(
                QgsAttributeEditorField(field, attachments.fields().indexOf(field), attachment_container)
            )
        attachments.setEditFormConfig(attachment_form)

        crop_index = parcels.fields().indexOf("crop_type")
        parcels.setEditorWidgetSetup(crop_index, QgsEditorWidgetSetup("TextEdit", {}))
        for field, alias in {
            "parcel_id": "كود القطعة", "holder_name": "اسم الحائز",
            "holding_number": "رقم الحيازة", "national_id": "الرقم القومي",
            "crop_type": "نوع الزرع",
        }.items():
            parcels.setFieldAlias(parcels.fields().indexOf(field), alias)
        parcel_form = parcels.editFormConfig()
        parcel_form.setLayout(QgsEditFormConfig.TabLayout)
        parcel_form.invisibleRootContainer().clear()
        parcel_container = QgsAttributeEditorContainer("Parcel", parcel_form.invisibleRootContainer())
        parcel_form.invisibleRootContainer().addChildElement(parcel_container)
        parcel_container.addChildElement(QgsAttributeEditorRelation(relation.id(), parcel_container))
        for field in ("crop_type", "parcel_id", "holder_name", "holding_number", "national_id"):
            parcel_container.addChildElement(
                QgsAttributeEditorField(field, parcels.fields().indexOf(field), parcel_container)
            )
        for field in ("parcel_id", "holder_name", "holding_number", "national_id"):
            parcel_form.setReadOnly(parcels.fields().indexOf(field), True)
        parcels.setEditFormConfig(parcel_form)
        project.layerTreeRoot().findLayer(attachments.id()).setItemVisibilityChecked(False)
        project.layerTreeRoot().findLayer(points.id()).setItemVisibilityChecked(False)
        if not project.write(str(project_path)):
            raise RuntimeError("QGIS could not save the QField project.")
    finally:
        app.exitQgis()


if __name__ == "__main__":
    main()
