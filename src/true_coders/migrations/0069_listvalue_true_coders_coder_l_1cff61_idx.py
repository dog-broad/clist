# Generated by Django 4.2.7 on 2024-02-17 13:04

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('true_coders', '0068_delete_virtualstart'),
    ]

    operations = [
        migrations.AddIndex(
            model_name='listvalue',
            index=models.Index(fields=['coder_list', 'group_id'], name='true_coders_coder_l_1cff61_idx'),
        ),
    ]