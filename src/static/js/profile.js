$(function() {
  $('#expand-ratings').click(function() {
    $(this).closest('div.col-lg-6').removeClass('col-lg-6').addClass('col-xs-12')
    $('#list-accounts').closest('div.col-lg-6').removeClass('col-lg-6').addClass('col-xs-12')
    $('#collapse-history-resources').click()
    $(this).remove()
    event.preventDefault()
  })

  $('.update-account').click(function() {
    var btn = $(this)
    var icon = btn.find('i')
    icon.addClass('fa-spin')
    $.ajax({
      type: 'POST',
      url: change_url,
      data: {
        pk: coder_pk,
        name: 'update-account',
        id: $(this).attr('data-account-id'),
      },
      success: function(data) {
        btn.attr('disabled', 'disabled')
        location.reload()
      },
      error: function(response) {
        icon.removeClass('fa-spin')
        log_ajax_error(response)
      },
    })
    event.preventDefault()
  })

  $.ajax({
    url: ratings_url,
    method: 'GET',
    dataType: 'json',
    success: function (response) {
      for (var resource in response['data']['resources']) {
        var resource_info = response['data']['resources'][resource]
        var resource_rating_id = 'resource_' + resource_info['pk'] + '_rating'

        if (resource_info['kind']) {
          resource_rating_id += '_' + resource_info['kind']
        }

        var canvas = $('#' + resource_rating_id)
        if (!canvas.length) {
          continue;
        }
        canvas.siblings('.loading_rating').remove()

        var resource_dates = response['data']['dates']
        config = create_chart_config(resource_info, resource_dates)

        var ctx = new Chart(resource_rating_id, config)
        add_selection_chart_range(resource_rating_id, ctx)

        if (!resource_info['kind']) {
          var resource_fields_id = 'resource_' + resource_info['pk'] + '_fields'
          add_selection_chart_fields(resource_info, resource_rating_id, resource_fields_id, resource_dates)
        }
      }
      $('.loading_rating').parent().remove()
    },
    error: function(data) {
      $.notify('{status} {statusText}'.format(data), 'error');
      $('.loading_rating').parent().remove()
    },
  });
})
