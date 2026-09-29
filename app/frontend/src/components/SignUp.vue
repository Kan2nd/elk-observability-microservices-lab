<template>
    <div>
        <app-nav></app-nav>
        <div class='container'>
            <spinner v-show='signingUp' message='Creating account...'></spinner>
            <form class='form-horizontal' role='form' v-on:submit.prevent='doSignup'>
                <div class='row'>
                    <div class='col-md-3'></div>
                    <div class='col-md-6'>
                        <h2>Create Account</h2>
                        <hr>
                    </div>
                </div>
                <div class='row'>
                    <div class='col-md-3'></div>
                    <div class='col-md-6'>
                        <div class='form-group has-danger'>
                            <label class='sr-only' for='username'>Username</label>
                            <div class='input-group mb-2 mr-sm-2 mb-sm-0'>
                                <div class='input-group-addon' style='width: 2.6rem'><i class='fa fa-at'></i></div>
                                <input
                                  type='text'
                                  name='username'
                                  class='form-control'
                                  placeholder='Choose a username'
                                  v-model='form.username'
                                  required
                                  autofocus
                                >
                            </div>
                        </div>
                    </div>
                </div>
                <div class='row'>
                    <div class='col-md-3'></div>
                    <div class='col-md-6'>
                        <div class='form-group'>
                            <label class='sr-only' for='firstname'>First Name</label>
                            <div class='input-group mb-2 mr-sm-2 mb-sm-0'>
                                <div class='input-group-addon' style='width: 2.6rem'><i class='fa fa-user'></i></div>
                                <input
                                  type='text'
                                  name='firstname'
                                  class='form-control'
                                  placeholder='First Name'
                                  v-model='form.firstname'
                                >
                            </div>
                        </div>
                    </div>
                </div>
                <div class='row'>
                    <div class='col-md-3'></div>
                    <div class='col-md-6'>
                        <div class='form-group'>
                            <label class='sr-only' for='lastname'>Last Name</label>
                            <div class='input-group mb-2 mr-sm-2 mb-sm-0'>
                                <div class='input-group-addon' style='width: 2.6rem'><i class='fa fa-user'></i></div>
                                <input
                                  type='text'
                                  name='lastname'
                                  class='form-control'
                                  placeholder='Last Name'
                                  v-model='form.lastname'
                                >
                            </div>
                        </div>
                    </div>
                </div>
                <div class='row'>
                    <div class='col-md-3'></div>
                    <div class='col-md-6'>
                        <div class='form-group'>
                            <label class='sr-only' for='password'>Password</label>
                            <div class='input-group mb-2 mr-sm-2 mb-sm-0'>
                                <div class='input-group-addon' style='width: 2.6rem'><i class='fa fa-key'></i></div>
                                <input
                                  type='password'
                                  name='password'
                                  class='form-control'
                                  placeholder='At least 4 characters'
                                  v-model='form.password'
                                  required>
                            </div>
                        </div>
                    </div>
                </div>
                <div class='row' style='padding-top: 1rem'>
                    <div class='col-md-3'></div>
                    <div class='col-md-6'>
                        <div class='form-control-feedback'>
                            <span class='text-danger align-middle'>
                            {{ errorMessage }}
                            </span>
                            <span class='text-success align-middle'>
                            {{ successMessage }}
                            </span>
                        </div>
                    </div>
                </div>
                <div class='row' style='padding-top: 1rem'>
                    <div class='col-md-3'></div>
                    <div class='col-md-6'>
                        <button type='submit' class='btn btn-success'><i class='fa fa-user-plus'></i> Sign Up</button>
                        <a href='#/login' class='btn btn-secondary' style='margin-left: 10px;'>Back to Login</a>
                    </div>
                </div>
            </form>
        </div>
    </div>
</template>

<script>
import Spinner from '@/components/common/Spinner'
import AppNav from '@/components/AppNav'

export default {
  name: 'signup',
  components: { AppNav, Spinner },
  methods: {
    doSignup: function () {
      this.signingUp = true
      this.errorMessage = ''
      this.successMessage = ''

      const signupData = {
        username: this.form.username,
        password: this.form.password,
        firstname: this.form.firstname,
        lastname: this.form.lastname
      }

      this.$http.post('/signup', signupData).then(response => {
        this.signingUp = false
        if (response.status === 201) {
          this.successMessage = 'Account created successfully! Redirecting to login...'
          setTimeout(() => {
            this.$router.push({ name: 'login' })
          }, 2000)
        } else {
          this.errorMessage = response.body.message || 'Signup failed'
        }
      }).catch(errorResponse => {
        this.signingUp = false
        if (errorResponse.body && errorResponse.body.message) {
          this.errorMessage = errorResponse.body.message
        } else if (errorResponse.status === 409) {
          this.errorMessage = 'Username already exists'
        } else {
          this.errorMessage = 'An error occurred during signup'
        }
      })
    }
  },
  data () {
    return {
      form: {
        username: '',
        password: '',
        firstname: '',
        lastname: ''
      },
      signingUp: false,
      errorMessage: '',
      successMessage: ''
    }
  }
}
</script>
