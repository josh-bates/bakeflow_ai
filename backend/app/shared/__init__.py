"""Shared model and services used by every module (architecture §6.1, §6.2).

Home of ``Record``, ``RecordRef``, ``Event``, ``TaskRequest`` and
``TaskResult``, ``ApprovalService``, ``ValidatedRecordStore`` and the domain
schemas. Modules import these instead of each other's private tables.
"""
