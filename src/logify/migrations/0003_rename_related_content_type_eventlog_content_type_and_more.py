# Generated by Django 4.2.3 on 2023-09-23 20:57

from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('logify', '0002_eventlog_message_alter_eventlog_status'),
    ]

    operations = [
        migrations.RenameField(
            model_name='eventlog',
            old_name='related_content_type',
            new_name='content_type',
        ),
        migrations.RenameField(
            model_name='eventlog',
            old_name='related_object_id',
            new_name='object_id',
        ),
    ]