# Generated by Django 2.2.13 on 2020-08-01 22:04

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('true_coders', '0029_auto_20200429_1924'),
    ]

    operations = [
        migrations.AddField(
            model_name='coder',
            name='n_accounts',
            field=models.IntegerField(db_index=True, default=0),
        ),
    ]